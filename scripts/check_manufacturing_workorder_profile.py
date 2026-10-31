"""Read-only M0 integrity checks; no database, HTTP, embeddings or model calls."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.manufacturing.contracts import (
    ApplicabilityRule, FIXTURE_VERSION, ManufacturingQuestion, WorkOrderFixtures, contract_schemas,
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def contained(root, relative):
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise ValueError("invalid repository-relative path")
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("path escapes repository")
    return path


def read(root, relative):
    return json.loads(contained(root, relative).read_bytes())


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def source_text(path):
    if path.suffix == ".docx":
        from docx import Document
        doc = Document(path)
        return "\n".join([p.text for p in doc.paragraphs] +
            [" | ".join(c.text for c in row.cells) for table in doc.tables for row in table.rows])
    if path.suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return path.read_text(encoding="utf-8-sig")


def check(root=ROOT):
    root = Path(root).resolve()
    profile = read(root, "evals/manufacturing_workorder_v1/profile.json")
    if profile["schema_version"] != "manufacturing-workorder-profile-v1" or profile["version"] != FIXTURE_VERSION:
        raise ValueError("unknown profile version")
    for relative, expected in profile["files_sha256"].items():
        if sha(contained(root, relative).read_bytes()) != expected:
            raise ValueError(f"frozen file changed: {relative}")
    for relative, expected in profile["preserved_files_sha256_lf"].items():
        if sha(contained(root, relative).read_bytes().replace(b"\r\n", b"\n")) != expected:
            raise ValueError(f"historical criterion changed: {relative}")
    manifest = read(root, profile["corpus_manifest"])
    if manifest["version"] != FIXTURE_VERSION:
        raise ValueError("corpus version mismatch")
    origin = manifest["source_manifest"]
    if sha(contained(root, origin["file"]).read_bytes()) != origin["sha256"]:
        raise ValueError("original source manifest changed")
    documents = {d["id"]: d for d in manifest["documents"]}
    if len(documents) != 13 or len(manifest["documents"]) != 13:
        raise ValueError("expected 13 unique documents")
    paths = [d["file"] for d in documents.values()]
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate source path")
    new_root = contained(root, "sample_docs/manufacturing_workorder_v1/documents")
    actual_new = {p.resolve() for p in new_root.rglob("*") if p.is_file()}
    declared_new = {contained(root, p) for p in paths
                    if p.startswith("sample_docs/manufacturing_workorder_v1/documents/")}
    if actual_new != declared_new:
        raise ValueError("unregistered file in new source document directory")
    texts = {}
    for identifier, doc in documents.items():
        path = contained(root, doc["file"])
        raw = path.read_bytes()
        if sha(raw) != doc["sha256"] or len(raw) != doc["bytes"]:
            raise ValueError(f"source changed: {identifier}")
        if doc["applicability_review_state"] != "draft":
            raise ValueError("offline annotation cannot grant publication approval")
        texts[identifier] = normalized(source_text(path))
    fixtures = WorkOrderFixtures.model_validate(read(root, profile["fixtures_file"]))
    if fixtures.fixture_version != FIXTURE_VERSION or len(fixtures.records) != 8:
        raise ValueError("expected eight versioned work orders")
    records = {r.work_order_id: r for r in fixtures.records}
    if set(records) != {f"WO-{i:03}" for i in range(17, 25)}:
        raise ValueError("unexpected work order identities")
    rules = [ApplicabilityRule.model_validate(r) for r in read(root, profile["rules_file"])["rules"]]
    if len(rules) != 5 or len({r.rule_id for r in rules}) != 5:
        raise ValueError("expected five unique source rules")
    for rule in rules:
        if rule.source_document_id not in texts or normalized(rule.source_quote) not in texts[rule.source_document_id]:
            raise ValueError(f"rule evidence missing: {rule.rule_id}")
        targets = [d for d in documents.values() if d["document_code"] == rule.target_document_code
                   and d["business_revision"] == rule.target_business_revision]
        if len(targets) != 1 or any(w not in records for w in rule.work_order_ids):
            raise ValueError(f"invalid rule target: {rule.rule_id}")
    if read(root, profile["schema_file"]) != contract_schemas():
        raise ValueError("contract schema drift; version the change explicitly")
    rows, split_groups = [], {}
    for split, expected_count in (("development", 16), ("regression", 24)):
        definition = profile["splits"][split]
        raw = contained(root, definition["file"]).read_bytes()
        selected = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
        if (len(selected) != expected_count or definition["count"] != expected_count or
                sha(raw) != definition["sha256"] or [r["id"] for r in selected] != definition["ids"]):
            raise ValueError(f"split integrity mismatch: {split}")
        if any(r["split"] != split for r in selected):
            raise ValueError("wrong split label")
        split_groups[split] = {r["group_key"] for r in selected}
        rows.extend(selected)
    if split_groups["development"] & split_groups["regression"]:
        raise ValueError("case group leaks across splits")
    if len({r["id"] for r in rows}) != 40:
        raise ValueError("duplicate case ID")
    work_order_splits = {}
    for row in rows:
        binding = row["business_context_binding"]
        record = records[binding["work_order_id"]]
        if (binding["fixture_version"] != fixtures.fixture_version or
                binding["source_system_id"] != record.source_system_id or
                binding["source_revision"] != record.source_revision):
            raise ValueError(f"business source binding mismatch: {row['id']}")
        previous = work_order_splits.setdefault(record.work_order_id, row["split"])
        if previous != row["split"]:
            raise ValueError("same work order leaks across splits")
        ManufacturingQuestion.model_validate(dict(question=row["question"], work_order_id=record.work_order_id))
        if (row["input_kind"] != "manufacturing" or not row["reference_answer"].strip() or
                row["answerable"] != (row["expected_behavior"] == "answer")):
            raise ValueError(f"invalid case contract: {row['id']}")
        actual = record.model_dump(mode="json")
        for evidence in row["expected_business_evidence"]:
            if evidence["field"] not in actual or actual[evidence["field"]] != evidence["value"]:
                raise ValueError(f"business field evidence mismatch: {row['id']}")
        expected_names = []
        for evidence in row["expected_evidence"]:
            identifier = evidence["document_id"]
            if identifier not in documents or normalized(evidence["text"]) not in texts[identifier]:
                raise ValueError(f"document evidence missing: {row['id']}/{identifier}")
            name = documents[identifier]["file"].rsplit("/", 1)[-1]
            if name != evidence["document"]:
                raise ValueError("source document identity mismatch")
            if name not in expected_names:
                expected_names.append(name)
        if row["required_source_documents"] != expected_names:
            raise ValueError("required source scope mismatch")
    acceptance = profile["acceptance"]
    if Counter(r["category"] for r in rows) != acceptance["expected_category_counts"]:
        raise ValueError("category counts mismatch")
    if Counter(r["expected_behavior"] for r in rows) != acceptance["expected_behavior_counts"]:
        raise ValueError("behavior counts mismatch")
    critical = {r["critical_id"]: r["id"] for r in rows if r["critical_id"]}
    if (critical != acceptance["critical_cases"] or len(critical) != 12 or
            set(critical) != {f"C{i:02}" for i in range(1, 13)}):
        raise ValueError("critical case set mismatch")
    expected_gates = dict(total=40, minimum_proxy_passed=36, minimum_review_passed=36,
        minimum_split_passed=dict(development=14, regression=22),
        all_numeric_conditions_required=True, all_behavior_cases_required=True,
        access_checks=20, failure_checks=12, ui_conversation_checks=8)
    if any(acceptance.get(k) != v for k, v in expected_gates.items()):
        raise ValueError("acceptance gate drift")
    # A recomputed manifest must not legitimize edited questions, labels or scope.
    from scripts.build_manufacturing_workorder_profile import definitions
    for relative, expected in definitions(root).items():
        if contained(root, relative).read_bytes() != expected:
            raise ValueError(f"pack definition drift; create a new version: {relative}")
    return dict(stage="M0", status="passed", documents=13, source_rules=len(rules),
        work_orders=len(records), questions=len(rows), splits={k: len([r for r in rows if r["split"] == k])
        for k in split_groups}, critical_cases=len(critical), model_calls=0,
        limitation="offline contracts and source bindings only; no runtime or answer-quality acceptance")
