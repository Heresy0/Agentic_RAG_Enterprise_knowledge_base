"""Verify real parser/evaluator contracts and safe reproducible generation."""
import hashlib
import json
from pathlib import Path

import pytest

from backend.app.documents.policy_metadata import extract_policy
from backend.app.documents.text_splitter import split_markdown
from backend.app.evaluation.metrics import evidence_metrics, score_answer
from backend.app.evaluation.offline import evaluate_parsing
from scripts.build_knowledge_expansion import (
    CORPUS, build_artifacts, validate_inventory, write_artifacts,
)
from scripts.corpus_expansion_data import DOCUMENTS
from scripts.evaluate_answers import load_dataset


@pytest.fixture(scope="module")
def cases():
    return [case for case in load_dataset(Path(__file__).resolve().parents[1] / 'evals/archive/xinghai_v3/single_turn.jsonl')
            if case["id"].startswith("CC-")]


@pytest.fixture(scope="module")
def manifest():
    return json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))


def test_all_documents_and_evidence_survive_actual_parser(cases):
    report = evaluate_parsing(CORPUS, cases)
    assert report["records"]
    assert all(row["status"] == "passed" for row in report["records"])
    assert len([r for r in report["records"] if r["kind"] == "document"]) == 20
    assert len([r for r in report["records"] if r["kind"] == "evidence"]) == 18


@pytest.mark.parametrize("number", [doc["number"] for doc in DOCUMENTS])
def test_policy_header_preserves_version_and_validity(number, manifest):
    spec = next(doc for doc in DOCUMENTS if doc["number"] == number)
    entry = next(row for row in manifest["documents"] if row["document_code"] == spec["code"]
                 and row["business_version"] == spec["version"])
    path = CORPUS / entry["file"]
    parsed = split_markdown(path)
    policy = extract_policy("\n".join(chunk.page_content for chunk in parsed), spec["title"])
    assert policy["document_code"] == spec["code"]
    assert policy["business_version"] == spec["version"]
    assert policy["effective_from"] == spec["start"]
    if spec["end"]:
        assert policy["effective_to"] == spec["end"]
        assert policy["business_status"] == "历史归档"
    else:
        assert "effective_to" not in policy
        assert policy["business_status"] == "现行"


def test_manifest_has_only_ingestable_documents_and_matching_hashes(manifest):
    actual = {p.relative_to(CORPUS).as_posix() for p in (CORPUS / "documents").rglob("*") if p.is_file()}
    assert actual == {row["file"] for row in manifest["documents"]}
    assert len(actual) == 20
    for row in manifest["documents"]:
        raw = (CORPUS / row["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]
        assert 800 <= len(raw.decode("utf-8")) <= 1500
        assert "reference_answer" not in raw.decode("utf-8")


def test_cross_library_answers_require_every_supporting_source(cases, manifest):
    case = next(row for row in cases if row["id"] == "CC-016")
    assert set(case["source_groups"]) == {"公司公共", "人力资源部"}
    public = next(row for row in manifest["documents"] if row["file"].startswith("documents/01_")
                  and "50_" in row["file"])
    public_rows = [dict(document_name=Path(public["file"]).name, content=chunk.page_content)
                   for chunk in split_markdown(CORPUS / public["file"])]
    assert not evidence_metrics(case["expected_evidence"], public_rows)["all_evidence_present"]
    response = dict(answerable=True, answer=case["reference_answer"],
                    citations=[dict(document_name=Path(public["file"]).name)])
    assert not score_answer(case, response)["automatic_proxy_pass"]
    # Public routing points to the internal guide without exposing its numbers.
    text = (CORPUS / public["file"]).read_text(encoding="utf-8")
    assert "第15个自然日" not in text and "第30个自然日" not in text


def test_refusal_cases_are_distinct_from_deadline_and_unpriced_future(cases):
    refusals = [row for row in cases if not row["answerable"]]
    assert {row["id"] for row in refusals} == {"CC-019", "CC-020"}
    assert all(not row["expected_evidence"] and not row["required_facts"] for row in refusals)
    case = next(row for row in cases if row["id"] == "CC-020")
    assert not score_answer(case, dict(answerable=True, answer="10月16日关闭。", citations=[]))["automatic_proxy_pass"]


def test_regeneration_is_byte_identical_and_check_is_read_only(tmp_path):
    artifacts, _ = build_artifacts(tmp_path / "corpus")
    assert write_artifacts(artifacts) == len(artifacts)
    before = {path: path.stat().st_mtime_ns for path in artifacts}
    assert write_artifacts(artifacts) == 0
    assert write_artifacts(artifacts, check=True) == 0
    assert before == {path: path.stat().st_mtime_ns for path in artifacts}


def test_existing_changes_stop_generation_before_any_write(tmp_path):
    artifacts, _ = build_artifacts(tmp_path / "corpus")
    target = next(iter(artifacts))
    target.parent.mkdir(parents=True)
    target.write_text("用户修改，不得覆盖", encoding="utf-8")
    with pytest.raises(ValueError, match="Existing content differs"):
        write_artifacts(artifacts)
    assert target.read_text(encoding="utf-8") == "用户修改，不得覆盖"
    assert not (tmp_path / "corpus" / "manifest.json").exists()
    with pytest.raises(ValueError, match="missing or changed"):
        write_artifacts(artifacts, check=True)


def test_evaluation_is_not_generated_into_ingestion_directory(tmp_path):
    artifacts, _ = build_artifacts(tmp_path / "corpus")
    assert len(artifacts) == 23
    assert not any(path.name in {"single_turn.jsonl", "答案与证据.md", "coverage.json"} for path in artifacts)
    corpus = tmp_path / "corpus"
    (corpus / "documents").mkdir(parents=True)
    (corpus / "documents" / "answers.md").write_text("答案不得入库", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected files"):
        validate_inventory(corpus)


def test_checked_in_artifacts_match_generator():
    artifacts, _ = build_artifacts(CORPUS)
    assert write_artifacts({path: text for path, text in artifacts.items() if path.is_relative_to(CORPUS)}, check=True) == 0
