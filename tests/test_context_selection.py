from langchain_core.documents import Document

from backend.app.evaluation.trace import capture_trace
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.context_selection import select_context_chunks


def doc(text, index, source="project-a"):
    return Document(id=f"chunk-{source}-{index}", page_content=text, metadata={
        "source_id": source, "document_name": "项目记录", "section_path": "项目资料",
    })


def candidates():
    return [doc(text, index) for index, text in enumerate([
        "原计划3月1日验收，调整至3月8日。", "会议日期为3月8日。",
        "验证项|结果\n客户验收账号|完成|2025年3月7日确认", "文档编号PRJ-2025-01，状态现行。",
        "客户于2025年3月8日签署最终验收；缺陷仍计划修复。",
    ])]


def test_reserves_both_explicit_range_witnesses_with_original_budget():
    documents = [doc('一般信息' + str(i), i) for i in range(4)] + [
        doc('电压范围(0~10)V使用标准器A。', 4),
        doc('电压范围(10~40)V使用标准器B。', 5),
    ]
    selected, reservations = select_context_chunks(documents, question='0～10V和10～40V分别使用什么？', max_per_source=4)
    assert len(selected) == 4 and documents[0] in selected
    assert documents[4] in selected and documents[5] in selected
    assert len(reservations) == 2


def test_range_units_and_numeric_boundaries_are_not_interchanged():
    documents = [doc('一般信息' + str(i), i) for i in range(4)] + [
        doc('范围(100~150)mm。范围(150~3000)kPa。', 4),
    ]
    selected, reservations = select_context_chunks(documents, question='0～150mm和150～3000mm用什么？', max_per_source=4)
    assert selected == documents[:4] and not reservations


def test_single_range_and_other_sources_keep_existing_selection():
    documents = [doc('一般信息' + str(i), i) for i in range(4)] + [doc('范围(0~10)V。', 4)]
    selected, reservations = select_context_chunks(documents, question='0～10V用什么？', max_per_source=4)
    assert selected == documents[:4] and not reservations
    outside = doc('范围(10~40)V。', 0, source='other')
    selected, _ = select_context_chunks(documents + [outside], question='0～10V和10～40V用什么？', max_per_source=4)
    assert outside in selected and len([d for d in selected if d.metadata['source_id'] == 'project-a']) == 4


def test_reserves_actual_completion_without_increasing_source_or_character_budget():
    documents = candidates() + [doc("由于新增权限功能导致验收改期。", 0, "changes")]
    with capture_trace() as trace:
        context = ContextBuilder().build(documents, question="项目为何改期，最后何时验收？")
    assert any("签署最终验收" in item.content for item in context.items)
    assert len([item for item in context.items if item.chunk_id.startswith("chunk-project-a")]) == 4
    assert "由于新增权限" in context.items[-1].content
    assert context.total_characters <= 12000
    reservations = trace.stages["context_selection"][0]["reservations"]
    assert reservations and reservations[0]["included"]


def test_no_special_case_company_amount_or_date_and_negative_status_is_evidence():
    documents = [doc("修复计划为4月9日。", 0), doc("会议安排为4月9日。", 1),
                 doc("缺陷尚未修复。", 2)]
    context = ContextBuilder(max_chunks_per_source=2).build(documents, question="缺陷是否已经修复？")
    assert any("尚未修复" in item.content for item in context.items)
    assert len(context.items) == 2


def test_only_intervenes_at_source_cap_and_preserves_metadata_and_ordinary_queries():
    documents = candidates()
    for question in ("项目情况？", "文档版本是什么？", ""):
        selected, hints = select_context_chunks(documents, question=question, max_per_source=4)
        assert selected == documents[:4] and not hints
    selected, hints = select_context_chunks(documents, question="最后何时验收？", max_per_source=5)
    assert selected == documents and not hints


def test_planned_or_title_only_statement_does_not_become_completion_witness():
    documents = [doc("核心模块范围。", 0), doc("会议参加人员。", 1),
                 doc("文档：最终验收纪要\n章节：已完成验收\n预计3月9日完成验收。", 2)]
    selected, hints = select_context_chunks(documents, question="最后何时验收？", max_per_source=2)
    assert selected == documents[:2] and not hints


def test_reserved_witness_can_still_be_skipped_by_hard_character_budget():
    documents = [doc("范围说明。", 0), doc("已完成验收。" * 100, 1)]
    context = ContextBuilder(max_characters=100, max_chunks_per_source=1).build(documents, question="最终验收时间？")
    assert len(context.items) <= 1 and context.total_characters <= 100


def test_source_identity_uses_document_id_when_storage_metadata_is_missing():
    documents = [Document(id=f"{source}-{index}", page_content=f"正文{source}{index}",
                          metadata={"document_id": source})
                 for source in ("a", "b") for index in range(3)]
    result = ContextBuilder(max_chunks_per_source=2).build(documents)
    assert len(result.items) == 4
