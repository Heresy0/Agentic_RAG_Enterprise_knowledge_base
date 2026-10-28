"""Validate actual files, project splitters and labelled evidence without model calls."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.manufacturing_corpus_data import DOCUMENTS, EDITION, QUESTIONS


def normalized(text):
    import unicodedata
    return "".join(unicodedata.normalize("NFKC", text).split()).replace("|", "")


def inventory(folder):
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    root = (folder / "documents").resolve()
    paths, ids = set(), set()
    for row in manifest["documents"]:
        path = (folder / row["file"]).resolve()
        if not path.is_relative_to(root) or path in paths or row["id"] in ids:
            raise ValueError("清单路径越界或存在重复文档")
        paths.add(path)
        ids.add(row["id"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("文件缺失或哈希变化：" + row["id"])
    actual = {path.resolve() for path in root.rglob("*") if path.is_file()}
    formats = dict(Counter(row["format"] for row in manifest["documents"]))
    if actual != paths or len(paths) != manifest["total_documents"] or formats != manifest["counts"]:
        raise ValueError("清单与实际文件不一致")
    source = Path(__file__).with_name("manufacturing_corpus_data.py")
    if manifest["source_definition_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError("语料定义已变化，必须复核并生成新版本，不能仅更新清单哈希")
    return manifest


def validate(folder, dataset, *, ocr=False):
    from backend.app.documents.document_splitter import split_docx
    from backend.app.documents.pdf_splitter import split_pdf, extract_pdf_pages, PdfOcrUnavailableError
    from backend.app.documents.text_splitter import split_markdown, split_txt
    from scripts.evaluate_answers import load_dataset
    from backend.app.knowledge.rag import CHUNKING_VERSION

    manifest = inventory(folder)
    cases = load_dataset(dataset / "single_turn.jsonl")
    if cases != QUESTIONS:
        raise ValueError("题集与已登记人工标注不一致")
    chunks, records, checks = {}, [], []
    for row in manifest["documents"]:
        if "OCR必需" in row.get("features", []) and not ocr:
            records.append(dict(id=row["id"], status="skipped", reason="real_ocr_not_enabled"))
            continue
        try:
            parts = {"pdf": split_pdf, "docx": split_docx, "md": split_markdown, "txt": split_txt}[row["format"]](folder / row["file"])
            valid = bool(parts) and all(part.page_content.strip() and "section_path" in part.metadata
                                        and part.metadata.get("document_name") for part in parts)
            chunks[row["id"]] = parts
            records.append(dict(id=row["id"], status="passed" if valid else "failed", format=row["format"],
                                chunks=len(parts), chunk_types=sorted({p.metadata["chunk_type"] for p in parts}),
                                review_required=any(p.metadata.get("quality", {}).get("review_required") for p in parts)))
        except PdfOcrUnavailableError:
            records.append(dict(id=row["id"], status="skipped", reason="ocr_runtime_unavailable"))
        except Exception as exc:
            records.append(dict(id=row["id"], status="error", error_type=type(exc).__name__))

    by_name = {Path(row["file"]).name: chunks.get(row["id"]) for row in manifest["documents"]}
    for case in cases:
        for evidence in case["expected_evidence"]:
            parts = by_name.get(evidence["document"])
            state = "skipped" if parts is None else "passed" if any(
                normalized(evidence["text"]) in normalized(part.page_content) for part in parts) else "failed"
            checks.append(dict(id=case["id"], kind="evidence_anchor", document=evidence["document"], status=state))

    # Independently labelled complete rows: numbers alone cannot pass these checks.
    tuples = {
        "MFG-01": ["CAL-150|0至150 mm|0.01 mm|±0.02 mm", "MIC-025|0至25 mm|0.001 mm|±0.003 mm"],
        "MFG-03": ["IP-01|辅助尺寸检查点01|CAL-150|mm", "IP-24|辅助尺寸检查点24|MIC-025|mm"],
        "MFG-08": ["外径|19.95至20.05 mm，包含上下限|MIC-025", "孔深|12.00至12.10 mm，包含上下限|CAL-150"],
    }
    for identifier, expected in tuples.items():
        parts = chunks.get(identifier)
        checks.append(dict(id=identifier, kind="parameter_row_binding", status="skipped" if parts is None else
                           "passed" if all(any(normalized(row) in normalized(p.page_content) for p in parts)
                                           for row in expected) else "failed"))
    flow = chunks.get("MFG-02", [])
    cross_page = any(p.metadata.get("page_start") == 1 and p.metadata.get("page_end") == 2
                     and all(text in normalized(p.page_content) for text in ["必须同时满足", "生产班长", "书面批准"])
                     for p in flow)
    checks.append(dict(id="MFG-02", kind="cross_page_conditions", status="passed" if cross_page else "failed"))
    table_parts = [p for p in chunks.get("MFG-03", []) if p.metadata["chunk_type"] == "table"]
    repeated = len(table_parts) >= 2 and all("项目编号" in p.page_content and "必要备注" in p.page_content for p in table_parts)
    checks.append(dict(id="MFG-03", kind="long_table_headers", status="passed" if repeated else "failed"))
    md = chunks.get("MFG-11", [])
    code = [p for p in md if p.metadata["chunk_type"] == "code"]
    checks.append(dict(id="MFG-11", kind="code_fence_preserved", status="passed" if any(
        "#不是章节|也不是表格" in p.page_content and "记录示例" in p.metadata["section_path"] for p in code) else "failed"))
    source_scan = next(item for item in DOCUMENTS if item["scan"])
    truth = json.loads((folder / "validation/scan_ground_truth.json").read_text(encoding="utf-8"))
    scan_row = next(row for row in manifest["documents"] if row["id"] == source_scan["id"])
    if truth["source_sha256"] != scan_row["sha256"]:
        raise ValueError("扫描标注与原件不匹配")
    scan_parts = chunks.get(source_scan["id"])
    raw_ocr_checks, corrections = [], []
    if scan_parts is not None:
        # Read real OCR before the trusted correction registry. Never substitute
        # ground-truth text as the extractor, and report raw errors separately.
        raw = normalized("\n".join(page.text for page in extract_pdf_pages(folder / scan_row["file"])))
        raw_ocr_checks = [dict(field=field, matched=normalized(field) in raw) for field in truth["expected_fields"]]
        for part in scan_parts:
            for correction in part.metadata.get("ocr_corrections", []):
                if correction not in corrections:
                    corrections.append(correction)
        text = normalized("\n".join(p.page_content for p in scan_parts))
        for field in truth["expected_fields"]:
            checks.append(dict(id=source_scan["id"], kind="reviewed_ocr_field", field=field,
                               status="passed" if normalized(field) in text else "failed"))
    summary = Counter(row["status"] for row in [*records, *checks])
    status = "failed" if summary["failed"] or summary["error"] else "incomplete" if summary["skipped"] else "passed"
    return dict(edition=EDITION, evaluation_type="offline_actual_splitters_not_answer_accuracy", status=status,
                chunking_version=CHUNKING_VERSION,
                corpus_manifest_sha256=hashlib.sha256((folder / "manifest.json").read_bytes()).hexdigest(),
                dataset_sha256=hashlib.sha256((dataset / "single_turn.jsonl").read_bytes()).hexdigest(),
                document_count=len(records), question_count=len(cases), formats=manifest["counts"],
                summary=dict(summary), documents=records, checks=checks,
                raw_ocr=dict(evaluation_type="real_ocr_before_trusted_corrections", fields=raw_ocr_checks,
                             matched_fields=sum(row["matched"] for row in raw_ocr_checks),
                             total_fields=len(raw_ocr_checks)),
                trusted_ocr_corrections=corrections, external_model_calls=0,
                limitation="仅验证已标注内容，不代表未标注版面、OCR全文或真实问答准确率。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "sample_docs/manufacturing_lite")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/manufacturing_lite")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.report and args.report.resolve().is_relative_to((args.corpus / "documents").resolve()):
        parser.error("报告不得写入可上传业务文档目录")
    result = validate(args.corpus, args.dataset, ocr=args.ocr)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"documents", "checks"}}, ensure_ascii=False))
    return 1 if result["status"] == "failed" else 2 if result["status"] == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
