"""Build 50 development / 100 frozen test labels without model calls or uploads."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_manufacturing_public import dump, sha
from scripts.manufacturing_public_questions import POSITIVE, NEGATIVE
from scripts.validate_manufacturing_public import inventory


def normalized(text):
    text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[A-Za-z])", "", text)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).replace("|", " ")).strip().casefold()


def source_text(corpus, row, page):
    if row["format"] == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(corpus / row["file"])
        if not isinstance(page, int) or not 1 <= page <= len(reader.pages):
            raise ValueError("A physical PDF page is required")
        return reader.pages[page - 1].extract_text()
    return (corpus / row["file"]).read_text(encoding="utf-8")


def build_cases(corpus):
    manifest = inventory(corpus)
    by_id = {row["id"]: row for row in manifest["documents"]}
    baseline = ROOT / "evals/manufacturing_lite/single_turn.jsonl"
    development = [json.loads(line) for line in baseline.read_text(encoding="utf-8").splitlines() if line]
    test, annotations = [], []
    # These older cases already informed implementation. They can never become
    # blind acceptance cases by changing their IDs or shuffling rows.
    for row in development:
        annotations.append(dict(id=row["id"], split="development", baseline=True,
                                concept_id="baseline:" + row["id"], evidence_locators=[]))
    for index, spec in enumerate(POSITIVE, 1):
        from scripts.evaluate_answers import normalize
        if any(normalize(fact) not in normalize(spec["reference_answer"]) for fact in spec["required_facts"]):
            raise ValueError("Required fact absent from reference answer: " + spec["concept_id"])
        row = by_id[spec["source_id"]]
        anchor = normalized(spec["anchor"])
        if len(anchor) < 4 or anchor not in normalized(source_text(corpus, row, spec["page"])):
            raise ValueError("Original evidence not found: " + spec["concept_id"])
        identity = f"MFG2-Q{index:03}"
        evidence = dict(document=Path(row["file"]).name, text=spec["anchor"])
        case = dict(id=identity, question=spec["question"], reference_answer=spec["reference_answer"],
                    answerable=True, required_facts=spec["required_facts"], expected_evidence=[evidence],
                    source_groups=[row["group"]], expected_scope=row["group"], source_file_paths=[row["file"]],
                    required_source_documents=[evidence["document"]], citation_requirement="all_supporting_sources",
                    category="公开技术依据", difficulty="medium", annotation_version="manufacturing-public-v2",
                    profile="全范围测试用户", manual_review_required=True,
                    semantic_checks=[spec["reference_answer"]], optional_facts=[], forbidden_claims=[])
        locator = dict(source_id=row["id"], document=row["file"], source_url=row["source_url"],
                       physical_page=spec["page"], anchor=spec["anchor"], source_sha256=row["sha256"])
        annotations.append(dict(id=identity, split=row["split"], baseline=False,
                                concept_id=spec["concept_id"], source_family=row["source_family"], evidence_locators=[locator]))
        (development if row["split"] == "development" else test).append(case)
    for index, (concept, behavior, question, answer, missing) in enumerate(NEGATIVE, 1):
        identity = f"MFG2-N{index:02}"
        test.append(dict(id=identity, question=question, reference_answer=answer, answerable=False,
                         required_facts=[], expected_evidence=[], source_groups=["公共参考", "生产工艺", "质量检验"],
                         expected_scope="全部可访问", source_file_paths=[], required_source_documents=[],
                         citation_requirement="none", category="证据不足" if behavior == "refuse" else "条件缺失",
                         difficulty="medium", annotation_version="manufacturing-public-v2", profile="全范围测试用户",
                         expected_behavior=behavior, missing_information=missing, manual_review_required=True,
                         semantic_checks=[answer], optional_facts=[], forbidden_claims=["未经证据确认具体结果或已执行操作"]))
        annotations.append(dict(id=identity, split="test", baseline=False, concept_id=concept, evidence_locators=[]))
    if len(development) != 50 or len(test) != 100 or len(POSITIVE) != 105 or len(NEGATIVE) != 25:
        raise ValueError("Expected 20 baseline + 105 public positive + 25 negative = 50/100")
    return {"development": development, "test": test}, annotations


def build(corpus, output):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Existing frozen evaluations are never overwritten")
    splits, annotations = build_cases(corpus)  # Validate all source facts before writing.
    for name, cases in splits.items():
        path = output / name / "single_turn.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("\n".join(json.dumps(row, ensure_ascii=False) for row in cases) + "\n").encode())
    dump(output / "annotations.json", annotations)
    lock = ROOT / "sample_docs/manufacturing_public_v2.sources.json"
    baseline = ROOT / "evals/manufacturing_lite/single_turn.jsonl"
    freeze = dict(edition="manufacturing-public-v2", status="frozen_before_first_v2_model_run",
                  corpus_manifest_sha256=sha((corpus / "manifest.json").read_bytes()), source_lock_sha256=sha(lock.read_bytes()),
                  baseline_dataset_sha256=sha(baseline.read_bytes()), annotations_sha256=sha((output / "annotations.json").read_bytes()),
                  counts={name: len(rows) for name, rows in splits.items()},
                  dataset_sha256={name: sha((output / name / "single_turn.jsonl").read_bytes()) for name in splits},
                  roles=dict(development="May guide tuning; includes the previously observed 20 cases.",
                             test="Held out from model-driven tuning; labels remain visible for audit, not a secret benchmark."),
                  negative_scoring="Refusal/clarification needs semantic review; answerable=false alone cannot distinguish them.",
                  provenance="Human-directed editorial labels grounded in locked source text; not independent expert certification.")
    dump(output / "splits.json", freeze)
    return freeze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "sample_docs/manufacturing_public_v2")
    parser.add_argument("--output", type=Path, default=ROOT / "evals/manufacturing_public_v2")
    args = parser.parse_args()
    print(json.dumps(build(args.corpus, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
