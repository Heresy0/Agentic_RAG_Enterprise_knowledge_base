import hashlib
import json
from pathlib import Path

import pytest

from scripts.evaluate_answers import load_dataset
from scripts.evaluate_system import parse_args
from scripts.merge_xinghai_evaluation import BASE_CORPUS, COMBINED_CORPUS, MAIN, merge, prepare_corpus


def test_default_entry_uses_one_80_case_pack_and_50_document_corpus():
    args = parse_args([])
    assert args.dataset_dir == MAIN
    assert args.corpus == COMBINED_CORPUS
    cases = load_dataset(MAIN / "single_turn.jsonl")
    assert len(cases) == len({row["id"] for row in cases}) == 80
    assert all(row["expected_scope"] in {"all_accessible", "公司公共", "人力资源部", "技术部", "平台研发部"} for row in cases)
    assert {row["as_of"] for row in cases if row["id"].startswith("XH-")} == {"2026-10-04"}
    assert {row["as_of"] for row in cases if row["id"].startswith("CC-")} == {"2026-10-13"}


def test_repeat_merge_preserves_original_cases_and_does_not_duplicate_books(tmp_path):
    if not (BASE_CORPUS / "manifest.json").exists():
        pytest.skip("Original local corpus is required")
    import shutil
    output = tmp_path / "pack"
    shutil.copytree(MAIN, output)
    before = [row for row in load_dataset(output / "single_turn.jsonl") if row["id"].startswith("XH-")]
    merge(output)
    once = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    merge(output)
    assert once == {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    assert before == [row for row in load_dataset(output / "single_turn.jsonl") if row["id"].startswith("XH-")]
    for book in ("测试题册.md", "答案与评分.md"):
        content = (output / book).read_text(encoding="utf-8")
        assert content.count("### CC-001 ·") == 1
        assert "XH-MT-02" in content and "ACL-12" in content
    assert (output / "conversations.json").read_bytes() == (MAIN / "conversations.json").read_bytes()
    assert (output / "permissions.json").read_bytes() == (MAIN / "permissions.json").read_bytes()
    with (output / "single_turn.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(id="CC-099")) + "\n")
    with pytest.raises(ValueError, match="must not be dropped"):
        merge(output)


def test_combined_corpus_keeps_every_original_file_hash(tmp_path):
    if not (BASE_CORPUS / "manifest.json").exists():
        pytest.skip("Original local corpus is required")
    output = tmp_path / "combined"
    manifest = prepare_corpus(output=output)
    assert manifest["total_documents"] == 50
    for row in manifest["documents"]:
        assert hashlib.sha256((output / row["file"]).read_bytes()).hexdigest() == row["sha256"]
    (output / "documents" / "answers.md").write_text("答案不得混入语料", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected files"):
        prepare_corpus(output=output)
