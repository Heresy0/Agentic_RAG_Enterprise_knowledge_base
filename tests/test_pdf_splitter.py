from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.documents.pdf_splitter import split_pdf
from backend.app.knowledge.rag import split_file


PDF_DIRECTORY = (
    Path(__file__).resolve().parent / "fixtures" / "pdfs"
)
STRUCTURED_PDF = PDF_DIRECTORY / "structured_policy.pdf"
SCANNED_PDF = PDF_DIRECTORY / "scanned_notice.pdf"


def test_numbered_requirements_remain_body_across_pages():
    from backend.app.documents.pdf_splitter import ExtractedPage, collect_pdf_units
    units = collect_pdf_units([
        ExtractedPage(1, "2 交接\n必须同时满足以下条件：\n1. 检查全部合格。\n2. 记录编号齐全。", "pypdf"),
        ExtractedPage(2, "3. 负责人签署。\n4. 暂停事项按书面要求处理。", "pypdf"),
    ])
    assert len(units) == 1
    unit = units[0]
    assert unit.kind == "rule_group"
    assert unit.section_path == ("2 交接",)
    assert (unit.page_start, unit.page_end) == (1, 2)
    for text in ("检查全部合格", "记录编号齐全", "负责人签署", "暂停事项"):
        assert text in unit.content


def test_explicit_pdf_heading_markers_and_numbered_headings_remain_headings():
    from backend.app.documents.pdf_splitter import _heading_level
    assert _heading_level("1.2 检查范围") == 2
    assert _heading_level("[[PDF_HEADING_1]]3. 注意事项。") == 1
    assert _heading_level("2. 记录编号齐全。") is None


def test_pdf_cleans_marginalia_and_keeps_cross_page_text() -> None:
    chunks = split_file(
        STRUCTURED_PDF,
        document_name="员工审批制度",
    )
    content = "\n".join(chunk.page_content for chunk in chunks)

    assert "星河科技员工审批制度" not in content
    assert "第 1 页" not in content
    assert (
        "跨页审批要求由申请人提交完整材料并说明"
        "业务理由后,部门负责人应在两个工作日内审批。"
        in content
    )
    assert all(
        chunk.page_content.startswith("文档：员工审批制度\n")
        for chunk in chunks
    )
    cross_page = next(
        chunk
        for chunk in chunks
        if "跨页审批要求" in chunk.page_content
    )
    assert cross_page.metadata["page_start"] == 1
    assert cross_page.metadata["page_end"] == 2
    assert cross_page.metadata["section_path"] == "1 总则"


def test_pdf_preserves_heading_context_and_columns() -> None:
    chunks = split_file(STRUCTURED_PDF)
    content = "\n".join(chunk.page_content for chunk in chunks)

    assert "章节：2 审批标准" in content
    assert "章节：补充规则" in content
    assert "费用编号必须唯一" in content
    assert "归档期限为五年" in content
    assert content.index("费用编号必须唯一") < content.index(
        "右栏规则"
    )
    column_chunk = next(
        chunk
        for chunk in chunks
        if chunk.metadata["section_path"] == "补充规则"
    )
    assert "pdfplumber-layout" in column_chunk.metadata["parser"]


def test_pdf_layout_parser_preserves_table_rows() -> None:
    pytest.importorskip("pdfplumber")
    chunks = split_file(STRUCTURED_PDF)
    table_chunks = [
        chunk
        for chunk in chunks
        if chunk.metadata["chunk_type"] == "table"
    ]

    assert table_chunks
    table_text = "\n".join(
        chunk.page_content for chunk in table_chunks
    )
    assert "事项|审批人|时限" in table_text
    assert "紧急采购|分管副总|4小时" in table_text
    assert all(
        "pdfplumber" in chunk.metadata["parser"]
        for chunk in table_chunks
    )


def test_image_only_pdf_uses_ocr_fallback() -> None:
    calls: list[tuple[Path, int]] = []

    def fake_ocr(path: Path, page_number: int) -> str:
        calls.append((path, page_number))
        return (
            "1 OCR 恢复结果\n"
            "扫描件审批编号为 OCR-2026-17。"
        )

    chunks = split_pdf(
        SCANNED_PDF,
        document_name="扫描审批通知",
        ocr_page=fake_ocr,
    )

    assert calls == [(SCANNED_PDF.resolve(), 1)]
    assert len(chunks) == 1
    assert "OCR-2026-17" in chunks[0].page_content
    assert chunks[0].metadata["parser"] == "ocr"
    assert chunks[0].metadata["section_path"] == "1 OCR 恢复结果"
