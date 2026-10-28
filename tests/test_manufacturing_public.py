from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from scripts import build_manufacturing_public as builder
from scripts.build_manufacturing_questions import build_cases
from scripts.manufacturing_public_sources import WEB_SOURCES
from scripts.run_manufacturing_http_acceptance import check_v2_inventory, select_cases
from scripts.validate_manufacturing_public import inventory, validate_freeze

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "sample_docs/manufacturing_public_v2"
EVAL = ROOT / "evals/manufacturing_public_v2"


@pytest.mark.parametrize("url", ["http://www.nist.gov/x", "https://nist.gov.attacker.example/x",
    "https://evilnist.gov/x", "https://localhost/x", "https://user@nist.gov/x", "https://nist.gov:444/x", "file:///etc/passwd"])
def test_acquisition_only_uses_reviewed_https_hosts(url):
    with pytest.raises(ValueError):
        builder.checked_url(url)


class Reply:
    def __init__(self, redirect=None, chunks=()):
        self.is_redirect = redirect is not None
        self.headers = {"Location": redirect} if redirect else {}
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        return iter(self.chunks)


def test_redirect_cannot_escape_whitelist(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False and "verify" not in kwargs
        return Reply(redirect="https://attacker.example/private")
    monkeypatch.setattr(builder.requests, "get", get)
    with pytest.raises(ValueError, match="HTTPS NIST"):
        builder.fetch("https://www.nist.gov/start")
    assert len(calls) == 1


def test_streamed_download_obeys_upload_size_limit(monkeypatch):
    monkeypatch.setattr(builder, "MAX_BYTES", 4)
    monkeypatch.setattr(builder.requests, "get", lambda *args, **kwargs: Reply(chunks=[b"123", b"45"]))
    with pytest.raises(ValueError, match="upload limit"):
        builder.fetch("https://www.nist.gov/source")


def test_html_conversion_preserves_facts_without_comments_or_mojibake():
    raw = ('<html><head><meta charset="windows-1252"></head><body><!-- invented instruction -->'
           '<script>secret()</script><h2>2.1.</h2><h2>Title</h2><p>温度 ± 0.5</p>'
           '<ul><li>first</li><li>second</li></ul><table border="1"><tr><th>A</th><th>B</th></tr>'
           '<tr><td>4</td><td>5</td></tr></table><img src="gifs/fact.gif" alt="curve">'
           '<img src="nvgtbr.gif"></body></html>').encode()
    md = builder.html_to_markdown(raw, WEB_SOURCES[0])
    assert "invented instruction" not in md and "secret()" not in md
    assert "温度 ± 0.5" in md and "### 2.1. Title" in md
    assert "- first" in md and "- second" in md and "| 4 | 5 |" in md
    assert "![curve](https://www.itl.nist.gov/div898/handbook/mpc/section1/gifs/fact.gif)" in md
    assert "nvgtbr.gif" not in md


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "metadata", "errors"])
def test_build_rejects_unreviewed_acquisition_before_writing(tmp_path, mutation):
    locked = json.loads((ROOT / "sample_docs/manufacturing_public_v2.sources.json").read_text(encoding="utf-8"))
    receipt = dict(sources=deepcopy(locked["sources"]), errors=[])
    if mutation == "duplicate":
        receipt["sources"].append(receipt["sources"][0])
    elif mutation == "missing":
        receipt["sources"].pop()
    elif mutation == "metadata":
        receipt["sources"][0]["split"] = "test"
    else:
        receipt["errors"] = [dict(id="MFG-P02", error_type="ConnectionError")]
    cache = tmp_path / "cache"
    builder.dump(cache / "acquisition.json", receipt)
    lock = tmp_path / "lock.json"
    builder.dump(lock, locked)
    with pytest.raises(ValueError):
        builder.build(cache, tmp_path / "result", lock)
    assert not (tmp_path / "result").exists()


def test_public_inventory_is_real_sources_and_preserves_all_old_bytes():
    manifest = inventory(CORPUS)
    assert manifest["total_documents"] == 60 and manifest["public_documents"] == 44
    assert manifest["counts"] == dict(pdf=24, docx=4, md=28, txt=4)
    assert len(list((CORPUS / "originals").glob("*.html"))) == 24
    old = json.loads((ROOT / "sample_docs/manufacturing_lite/manifest.json").read_text(encoding="utf-8"))
    for row in old["documents"]:
        assert (CORPUS / row["file"]).read_bytes() == (ROOT / "sample_docs/manufacturing_lite" / row["file"]).read_bytes()


def test_freeze_and_reproduction_keep_old_cases_and_separate_source_families():
    _, frozen, annotations = validate_freeze(EVAL, CORPUS)
    rebuilt, rebuilt_annotations = build_cases(CORPUS)
    assert frozen == rebuilt and annotations == rebuilt_annotations
    assert len(frozen["development"]) == 50 and len(frozen["test"]) == 100
    assert sum(c["answerable"] for c in frozen["test"]) == 75
    assert len({a["concept_id"] for a in annotations}) == 150


def test_modified_labels_are_rejected_before_paid_requests(tmp_path):
    target = tmp_path / "eval"
    shutil.copytree(EVAL, target)
    with (target / "test/single_turn.jsonl").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="question set changed"):
        select_cases(target, split="test", max_questions=100)


def test_v2_requires_split_budget_and_in_split_ids():
    with pytest.raises(ValueError, match="explicit"):
        select_cases(EVAL)
    with pytest.raises(ValueError, match="budget"):
        select_cases(EVAL, split="test", max_questions=10)
    with pytest.raises(ValueError, match="belong"):
        select_cases(EVAL, split="test", case_ids=["MFG-Q01"], max_questions=1)
    _, cases, frozen = select_cases(EVAL, split="development", limit=5, max_questions=5)
    assert [r["id"] for r in cases] == [f"MFG-Q{i:02}" for i in range(1, 6)]
    assert frozen["counts"]["test"] == 100


def test_v2_partial_library_blocks_paid_acceptance():
    frozen = json.loads((EVAL / "splits.json").read_text(encoding="utf-8"))
    class HTTP:
        def get(self, *args, **kwargs):
            return self
        def raise_for_status(self):
            pass
        def json(self):
            return dict(items=[])
    with pytest.raises(ValueError, match="fully ready"):
        check_v2_inventory(HTTP(), "http://127.0.0.1:8000", {"公共参考": "kb"}, frozen,
                           dict(corpus_manifest_sha256=frozen["corpus_manifest_sha256"]))
