"""Build M0's new immutable pack. Existing differing files are never overwritten."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.manufacturing.contracts import contract_schemas, FIXTURE_VERSION
from scripts.manufacturing_workorder_data import (
    SELECTED_IDS, NOTICES, notice_text, fixture_definition, rule_definitions, case_definitions,
)

CORPUS = "sample_docs/manufacturing_workorder_v1"
EVAL = "evals/manufacturing_workorder_v1"


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def definitions(root: Path) -> dict[str, bytes]:
    original_path = "sample_docs/manufacturing_lite/manifest.json"
    original_raw = (root / original_path).read_bytes()
    original = json.loads(original_raw)
    by_id = {d["id"]: d for d in original["documents"]}
    outputs, documents = {}, []
    roles = {"MFG-02": "flow", "MFG-06": "instruction", "MFG-07": "instruction",
             "MFG-08": "inspection", "MFG-09": "change_notice", "MFG-10": "routing",
             "MFG-12": "record_rule", "MFG-14": "event_record", "MFG-15": "event_record",
             "MFG-16": "inspection_record"}
    for identifier in SELECTED_IDS:
        source = by_id[identifier]
        path = "sample_docs/manufacturing_lite/" + source["file"]
        raw = (root / path).read_bytes()
        if sha(raw) != source["sha256"]:
            raise ValueError(f"original source hash changed: {identifier}")
        work_orders = {"MFG-14": ["WO-017"], "MFG-15": ["WO-018"], "MFG-16": ["WO-017"]}
        scope_kind = ("work_order" if identifier in work_orders else "company"
                      if identifier in ("MFG-02", "MFG-12") else "routing"
                      if identifier == "MFG-10" else "product_operation")
        documents.append(dict(id=identifier, file=path, sha256=sha(raw), bytes=len(raw),
            group=source["group"], source_kind=source["source_kind"],
            document_code=source["document_code"], business_revision=source["business_version"],
            effective_from=source["effective_from"], effective_to=source["effective_to"],
            document_role=roles[identifier], scope_kind=scope_kind,
            product_models=["HC-A17", "HC-A18"] if scope_kind == "routing" else
                           ["HC-A17"] if scope_kind in ("product_operation", "work_order") else [],
            operation_codes=["OP-20"] if scope_kind in ("product_operation", "work_order") else [],
            work_order_ids=work_orders.get(identifier, []), applicability_review_state="draft"))
    for notice in NOTICES:
        identifier, filename, code, work_order, _, _ = notice
        path = f"{CORPUS}/documents/生产工艺/{filename}"
        raw = notice_text(notice).encode("utf-8")
        outputs[path] = raw
        documents.append(dict(id=identifier, file=path, sha256=sha(raw), bytes=len(raw),
            group="生产工艺", source_kind="synthetic_business", document_code=code,
            business_revision="1.0", effective_from="2026-10-03", effective_to=None,
            document_role="change_notice", scope_kind="work_order", product_models=["HC-A17"],
            operation_codes=["OP-20"], work_order_ids=[work_order],
            applicability_review_state="draft"))
    outputs[f"{CORPUS}/manifest.json"] = encoded(dict(schema_version="manufacturing-corpus-v1",
        version=FIXTURE_VERSION, source_manifest=dict(file=original_path, sha256=sha(original_raw)),
        documents=documents, notice="合成场景；元数据为待审核标注，未导入运行库，非实际生产依据。"))
    outputs[f"{CORPUS}/work_orders.json"] = encoded(fixture_definition())
    outputs[f"{CORPUS}/rules.json"] = encoded(rule_definitions())
    outputs[f"{EVAL}/contracts.schema.json"] = encoded(contract_schemas())
    cases = case_definitions(documents)
    splits = {}
    for split in ("development", "regression"):
        selected = [row for row in cases if row["split"] == split]
        path = f"{EVAL}/{split}/single_turn.jsonl"
        raw = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                      for row in selected).encode("utf-8")
        outputs[path] = raw
        splits[split] = dict(file=path, count=len(selected), ids=[r["id"] for r in selected],
                             sha256=sha(raw))
    release_path = "evals/manufacturing_zh_lite_v1/demo_release.json"
    release = json.loads((root / release_path).read_bytes())
    preserved = dict(release["files_sha256_lf"])
    preserved[release_path] = sha((root / release_path).read_bytes().replace(b"\r\n", b"\n"))
    profile = dict(schema_version="manufacturing-workorder-profile-v1", version=FIXTURE_VERSION,
        baseline_ref="9846c4e", purpose="合成工单场景回归集；非独立企业盲测",
        corpus_manifest=f"{CORPUS}/manifest.json", fixtures_file=f"{CORPUS}/work_orders.json",
        rules_file=f"{CORPUS}/rules.json", schema_file=f"{EVAL}/contracts.schema.json",
        splits=splits, files_sha256={path: sha(raw) for path, raw in sorted(outputs.items())},
        preserved_files_sha256_lf=preserved,
        acceptance=dict(total=40, minimum_proxy_passed=36, minimum_review_passed=36,
            minimum_split_passed=dict(development=14, regression=22),
            expected_category_counts=dict(facts=6, version=10, cross_document=8,
                numeric_conditions=6, clarify=4, refuse=6),
            expected_behavior_counts=dict(answer=30, clarify=4, refuse=6),
            critical_cases={r["critical_id"]: r["id"] for r in cases if r["critical_id"]},
            all_numeric_conditions_required=True, all_behavior_cases_required=True,
            access_checks=20, failure_checks=12, ui_conversation_checks=8))
    outputs[f"{EVAL}/profile.json"] = encoded(profile)
    return outputs


def build(root: Path = ROOT) -> dict:
    root = root.resolve()
    outputs = definitions(root)
    # Preflight the entire batch before writing any new file.
    for relative, raw in outputs.items():
        path = root / relative
        if path.exists() and path.read_bytes() != raw:
            raise ValueError(f"existing frozen file differs; create a new pack version: {relative}")
    written = 0
    for relative, raw in outputs.items():
        path = root / relative
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(raw)
            written += 1
    return dict(version=FIXTURE_VERSION, created_files=written, total_files=len(outputs),
                documents=13, work_orders=8, questions=40)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(build(args.root), ensure_ascii=True))
