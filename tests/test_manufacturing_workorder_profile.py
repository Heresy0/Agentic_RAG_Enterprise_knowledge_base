import hashlib
import json
from pathlib import Path
import shutil

import pytest

from scripts.build_manufacturing_workorder_profile import build, CORPUS, EVAL
from scripts.check_manufacturing_workorder_profile import check, contained

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pack(tmp_path):
    shutil.copytree(ROOT / "sample_docs/manufacturing_lite", tmp_path / "sample_docs/manufacturing_lite")
    release = "evals/manufacturing_zh_lite_v1/demo_release.json"
    protected = json.loads((ROOT / release).read_bytes())["files_sha256_lf"]
    for relative in [release, *protected]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    build(tmp_path)
    return tmp_path


def write_profile(root, profile):
    (root / EVAL / "profile.json").write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")


def rehash(root, relative):
    profile = json.loads((root / EVAL / "profile.json").read_bytes())
    digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    profile["files_sha256"][relative] = digest
    for split in profile["splits"].values():
        if split["file"] == relative: split["sha256"] = digest
    write_profile(root, profile)


def change_case(root, update):
    relative = f"{EVAL}/development/single_turn.jsonl"
    path = root / relative
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    update(rows)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    rehash(root, relative)


def test_real_pack_is_source_bound_and_preserves_historical_release():
    result = check(ROOT)
    assert result["documents"] == 13 and result["source_rules"] == 5
    assert result["work_orders"] == 8 and result["questions"] == 40
    assert result["splits"] == {"development": 16, "regression": 24}
    assert result["model_calls"] == 0


def test_builder_is_repeatable_and_preflights_before_writing(pack):
    assert build(pack)["created_files"] == 0
    path = pack / CORPUS / "work_orders.json"
    path.write_bytes(path.read_bytes() + b" ")
    missing = pack / CORPUS / "documents/生产工艺/01_WO019书面切换通知.md"
    missing.unlink()
    with pytest.raises(ValueError, match="existing frozen file differs"):
        build(pack)
    assert not missing.exists(), "a rejected batch must not write earlier files"


def test_original_document_byte_change_is_rejected(pack):
    manifest = json.loads((pack / CORPUS / "manifest.json").read_bytes())
    original = pack / manifest["documents"][0]["file"]
    original.write_bytes(original.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="source changed"):
        check(pack)


def test_unregistered_document_cannot_enter_the_ingestion_directory(pack):
    path = pack / CORPUS / "documents/生产工艺/answers.txt"
    path.write_text("reference answers must not be ingested", encoding="utf-8")
    with pytest.raises(ValueError, match="unregistered file"):
        check(pack)


def test_historical_lf_hash_accepts_checkout_line_endings_but_not_content(pack):
    path = pack / "backend/app/evaluation/metrics.py"
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    path.write_bytes(raw.replace(b"\n", b"\r\n"))
    assert check(pack)["status"] == "passed"
    path.write_bytes(raw + b"# changed\n")
    with pytest.raises(ValueError, match="historical criterion changed"):
        check(pack)


@pytest.mark.parametrize("relative", ["../outside.json", "C:/outside.json", "/outside.json", "..\\outside.json"])
def test_profile_paths_cannot_escape_repository(tmp_path, relative):
    with pytest.raises(ValueError):
        contained(tmp_path, relative)


def test_group_leak_is_rejected_even_with_updated_file_hashes(pack):
    change_case(pack, lambda rows: rows[0].update(group_key="old-work-order-variants"))
    with pytest.raises(ValueError, match="group leaks"):
        check(pack)


def test_business_gold_cannot_drift_from_source_record(pack):
    change_case(pack, lambda rows: rows[0]["expected_business_evidence"][0].update(value="HC-A18"))
    with pytest.raises(ValueError, match="business field evidence"):
        check(pack)


def test_reference_change_is_not_legitimized_by_rehashing_manifest(pack):
    change_case(pack, lambda rows: rows[0].update(reference_answer="偷偷修改标准答案"))
    with pytest.raises(ValueError, match="pack definition drift"):
        check(pack)


def test_rule_quote_must_exist_in_original_document(pack):
    path = pack / CORPUS / "rules.json"
    rules = json.loads(path.read_bytes())
    quote = "不存在于原文的例外"
    rules["rules"][0].update(source_quote=quote, source_quote_sha256=hashlib.sha256(quote.encode()).hexdigest())
    path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    rehash(pack, f"{CORPUS}/rules.json")
    with pytest.raises(ValueError, match="rule evidence missing"):
        check(pack)


def test_acceptance_thresholds_cannot_be_silently_lowered(pack):
    profile = json.loads((pack / EVAL / "profile.json").read_bytes())
    profile["acceptance"]["minimum_proxy_passed"] = 1
    write_profile(pack, profile)
    with pytest.raises(ValueError, match="acceptance gate drift"):
        check(pack)
