"""Hash, real-parser and evidence-label validation; never calls a model or DB."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_manufacturing_public import sha, dump


def inventory(corpus):
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    root = (corpus / "documents").resolve()
    paths, ids, hashes = set(), set(), set()
    for row in manifest["documents"]:
        path = (corpus / row["file"]).resolve()
        if not path.is_relative_to(root) or path in paths or row["id"] in ids:
            raise ValueError("Duplicate identity or path outside upload directory")
        if not path.is_file() or sha(path.read_bytes()) != row["sha256"]:
            raise ValueError("Corpus file missing or changed: " + row["id"])
        if row.get("raw_sha256") in hashes:
            raise ValueError("Duplicate original source bytes")
        if row.get("raw_sha256"):
            hashes.add(row["raw_sha256"])
        paths.add(path); ids.add(row["id"])
        if row.get("kind") == "public_web":
            original = corpus / "originals" / (row["id"] + ".html")
            if sha(original.read_bytes()) != row["raw_sha256"]:
                raise ValueError("Original HTML fingerprint differs")
    if paths != {p.resolve() for p in root.rglob("*") if p.is_file()} or len(paths) != manifest["total_documents"]:
        raise ValueError("Actual inventory does not match the manifest")
    if dict(Counter(row["format"] for row in manifest["documents"])) != manifest["counts"]:
        raise ValueError("Format counts differ")
    return manifest


def parser_fingerprint():
    folder = ROOT / "backend/app/documents"
    return sha(b"".join(path.name.encode() + path.read_bytes() for path in sorted(folder.glob("*.py")))
               + (folder / "reviewed_ocr.json").read_bytes() + (ROOT / "requirements.txt").read_bytes())


def validate_freeze(evaluation, corpus):
    from scripts.evaluate_answers import load_dataset
    from scripts.build_manufacturing_questions import normalized
    manifest = inventory(corpus)
    frozen = json.loads((evaluation / "splits.json").read_text(encoding="utf-8"))
    lock = ROOT / "sample_docs/manufacturing_public_v2.sources.json"
    baseline = ROOT / "evals/manufacturing_lite/single_turn.jsonl"
    fingerprints = {"corpus_manifest_sha256": corpus / "manifest.json", "source_lock_sha256": lock,
                    "baseline_dataset_sha256": baseline, "annotations_sha256": evaluation / "annotations.json"}
    if any(frozen[key] != sha(path.read_bytes()) for key, path in fingerprints.items()):
        raise ValueError("Frozen corpus, sources, baseline or annotations changed")
    splits = {}
    for split, count in (("development", 50), ("test", 100)):
        path = evaluation / split / "single_turn.jsonl"
        if sha(path.read_bytes()) != frozen["dataset_sha256"][split]:
            raise ValueError("Frozen question set changed")
        splits[split] = load_dataset(path)
        if len(splits[split]) != count or frozen["counts"][split] != count:
            raise ValueError("Split size mismatch")
    if splits["development"][:20] != load_dataset(baseline):
        raise ValueError("Previously observed baseline must stay unchanged in development")
    cases = [row for rows in splits.values() for row in rows]
    annotations = json.loads((evaluation / "annotations.json").read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in annotations}
    if len(by_id) != 150 or len(annotations) != 150 or set(by_id) != {row["id"] for row in cases}:
        raise ValueError("Annotation identities differ from dataset")
    if len({row["id"] for row in cases}) != 150 or len({normalized(row["question"]) for row in cases}) != 150 or len({r["concept_id"] for r in annotations}) != 150:
        raise ValueError("Duplicate question identity, question text or concept")
    families = {}
    documents = {row["file"]: row for row in manifest["documents"]}
    for row in manifest["documents"]:
        families.setdefault(row["source_family"], set()).add(row["split"])
    if any(len(values) > 1 for values in families.values()):
        raise ValueError("Source family leaks across splits")
    for split, rows in splits.items():
        for case in rows:
            annotation = by_id[case["id"]]
            if annotation["split"] != split:
                raise ValueError("Case annotation has a different split")
            if any(documents[file]["split"] != split for file in case["source_file_paths"]):
                raise ValueError("Question references a source from another split")
            if not annotation["baseline"] and case["answerable"]:
                if not annotation["evidence_locators"]:
                    raise ValueError("Public case lacks a source locator")
                for locator in annotation["evidence_locators"]:
                    row = documents[locator["document"]]
                    if row["id"] != locator["source_id"] or row["sha256"] != locator["source_sha256"] or row["source_family"] != annotation["source_family"]:
                        raise ValueError("Source locator identity changed")
            if not case["answerable"] and not annotation["baseline"] and case.get("expected_behavior") not in ("refuse", "clarify"):
                raise ValueError("Negative case lacks an explicit semantic behavior")
    return frozen, splits, annotations


def validate_labels(evaluation, corpus, cache):
    from scripts.build_manufacturing_questions import normalized, source_text
    frozen, splits, annotations = validate_freeze(evaluation, corpus)
    docs = {row["file"]: row for row in inventory(corpus)["documents"]}
    fingerprint = parser_fingerprint()
    matches, failures, parsed = [], [], {}
    # Cache must bind to the final corpus and the parser actually being reviewed.
    for file, row in docs.items():
        saved = json.loads((cache / (row["id"] + ".json")).read_text(encoding="utf-8"))
        if saved["source_sha256"] != row["sha256"] or saved["parser_sha256"] != fingerprint or saved["chunking_version"] != "structured-v5" or saved["record"]["status"] != "passed":
            raise ValueError("Missing, stale or unsuccessful actual-parser cache: " + row["id"])
        parsed[file] = saved["chunks"]
    for annotation in annotations:
        for locator in annotation["evidence_locators"]:
            anchor = normalized(locator["anchor"])
            source_ok = anchor in normalized(source_text(corpus, docs[locator["document"]], locator["physical_page"]))
            hits = []
            for index, chunk in enumerate(parsed[locator["document"]]):
                text = normalized(chunk["content"])
                mode = "normalized" if anchor in text else "whitespace_folded" if anchor.replace(" ", "") in text.replace(" ", "") else None
                if mode:
                    hits.append(dict(index=index, content_sha256=sha(chunk["content"].encode()), match_mode=mode,
                                     metadata=chunk["metadata"]))
            record = dict(id=annotation["id"], source_id=locator["source_id"], physical_page=locator["physical_page"],
                          original_anchor_found=source_ok, actual_chunk_matches=hits)
            (matches if source_ok and hits else failures).append(record)
    cases = [case for rows in splits.values() for case in rows]
    return dict(evaluation_type="offline_source_and_actual_chunk_anchor_validation", external_model_calls=0,
                total_questions=len(cases), split_counts={name: len(rows) for name, rows in splits.items()},
                new_public_positive=sum(bool(row["evidence_locators"]) for row in annotations),
                legacy_cases_preserved=20, negative_behaviors=dict(Counter(case.get("expected_behavior") for case in cases if case.get("expected_behavior"))),
                matched=len(matches), failed=len(failures), corpus_manifest_sha256=frozen["corpus_manifest_sha256"],
                freeze_sha256=sha((evaluation / "splits.json").read_bytes()), parser_sha256=fingerprint,
                limitation="Anchor presence is not full sentence reconstruction, retrieval recall, answer accuracy or independent semantic review.",
                matches=matches, failures=failures)


def parse(corpus, cache, *, ocr=False, workers=2):
    from backend.app.documents.document_splitter import split_docx
    from backend.app.documents.pdf_splitter import split_pdf
    from backend.app.documents.text_splitter import split_markdown, split_txt
    from backend.app.knowledge.rag import CHUNKING_VERSION
    manifest = inventory(corpus)
    fingerprint = parser_fingerprint()
    cache.mkdir(parents=True, exist_ok=True)

    def one(row):
        target = cache / (row["id"] + ".json")
        if target.exists():
            saved = json.loads(target.read_text(encoding="utf-8"))
            if saved.get("source_sha256") == row["sha256"] and saved.get("parser_sha256") == fingerprint and saved.get("chunking_version") == CHUNKING_VERSION:
                return saved["record"]
        if "OCR必需" in row.get("features", []) and not ocr:
            return dict(id=row["id"], status="skipped", reason="real_ocr_not_enabled")
        try:
            chunks = {"pdf": split_pdf, "docx": split_docx, "md": split_markdown, "txt": split_txt}[row["format"]](corpus / row["file"])
            valid = bool(chunks) and all(c.page_content.strip() and "section_path" in c.metadata for c in chunks)
            record = dict(id=row["id"], status="passed" if valid else "failed", chunks=len(chunks),
                          chunk_types=sorted({c.metadata["chunk_type"] for c in chunks}),
                          ocr_chunks=sum("ocr" in c.metadata.get("parser", "") for c in chunks),
                          review_required=any(c.metadata.get("quality", {}).get("review_required") for c in chunks))
            dump(target, dict(source_sha256=row["sha256"], parser_sha256=fingerprint, chunking_version=CHUNKING_VERSION,
                              record=record, chunks=[dict(content=c.page_content, metadata=c.metadata) for c in chunks]))
        except Exception as exc:
            record = dict(id=row["id"], status="error", error_type=type(exc).__name__)
        print(json.dumps(record), flush=True)
        return record

    records = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(one, row) for row in manifest["documents"]]):
            records.append(future.result())
    records.sort(key=lambda row: row["id"])
    return dict(evaluation_type="offline_actual_project_splitters", external_model_calls=0,
                corpus_manifest_sha256=sha((corpus / "manifest.json").read_bytes()), parser_sha256=fingerprint,
                chunking_version=CHUNKING_VERSION, summary=dict(Counter(row["status"] for row in records)), records=records)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "sample_docs/manufacturing_public_v2")
    parser.add_argument("--parse-cache", type=Path, default=ROOT / "tmp/manufacturing-public-v2/parsed")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3, 4), default=2)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--labels", type=Path, help="Validate frozen labels against existing real-parser cache; do not parse again")
    args = parser.parse_args(argv)
    if args.report.resolve().is_relative_to((args.corpus / "documents").resolve()) or args.parse_cache.resolve().is_relative_to((args.corpus / "documents").resolve()):
        parser.error("Cache and report must remain outside the upload directory")
    result = validate_labels(args.labels, args.corpus, args.parse_cache) if args.labels else parse(args.corpus, args.parse_cache, ocr=args.ocr, workers=args.workers)
    dump(args.report, result)
    if args.labels:
        print(json.dumps({key: value for key, value in result.items() if key not in ("matches", "failures")}))
        return int(bool(result["failed"]))
    print(json.dumps({key: value for key, value in result.items() if key != "records"}))
    return 1 if result["summary"].get("failed") or result["summary"].get("error") else 2 if result["summary"].get("skipped") else 0


if __name__ == "__main__":
    raise SystemExit(main())
