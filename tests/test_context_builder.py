from langchain_core.documents import Document

import pytest

from backend.app.evaluation.trace import capture_trace
from backend.app.knowledge.context_builder import (
    BuiltContext,
    ContextBuilder,
)


def make_document(
    content: str,
    *,
    source: str = "C:/docs/policy.docx",
    source_id: str | None = None,
    document_id: str | None = None,
    document_name: str = "员工制度",
    section_path: str = "考勤 > 迟到",
    chunk_id: str | None = None,
    content_hash: str | None = None,
    rerank_score: float | None = None,
) -> Document:
    metadata = {
        "source": source,
        "document_name": document_name,
        "section_path": section_path,
    }

    if source_id is not None:
        metadata["source_id"] = source_id
    if document_id is not None:
        metadata["document_id"] = document_id
    if content_hash is not None:
        metadata["chunk_content_hash"] = content_hash
    if rerank_score is not None:
        metadata["rerank_score"] = rerank_score

    return Document(
        id=chunk_id,
        page_content=content,
        metadata=metadata,
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_characters": 0}, "max_characters"),
        ({"max_chunks_per_source": 0}, "max_chunks_per_source"),
    ],
)
def test_rejects_invalid_configuration(
    kwargs: dict,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ContextBuilder(**kwargs)


def test_empty_documents_return_empty_context() -> None:
    result = ContextBuilder().build([])

    assert result == BuiltContext.empty()


def test_build_preserves_rerank_order_and_metadata() -> None:
    documents = [
        make_document(
            "第一条规定。",
            document_id=(
                "00000000-0000-0000-0000-000000000001"
            ),
            chunk_id="chunk-a",
            rerank_score=0.98,
        ),
        make_document(
            "第二条规定。",
            source="C:/docs/travel.docx",
            document_name="差旅制度",
            section_path="住宿标准",
            chunk_id="chunk-b",
            rerank_score=0.87,
        ),
    ]

    result = ContextBuilder().build(documents)

    assert [item.citation_id for item in result.items] == [
        "资料1",
        "资料2",
    ]
    assert [item.chunk_id for item in result.items] == [
        "chunk-a",
        "chunk-b",
    ]
    assert result.items[0].document_id == (
        "00000000-0000-0000-0000-000000000001"
    )
    assert [item.rerank_score for item in result.items] == [
        0.98,
        0.87,
    ]
    assert "[资料1]" in result.text
    assert "[资料2]" in result.text
    assert result.text.index("第一条规定") < result.text.index(
        "第二条规定"
    )
    assert result.total_characters == len(result.text)


def test_deduplicates_by_hash_and_normalized_content() -> None:
    documents = [
        make_document("同一条 内容", content_hash="same"),
        make_document("完全不同文本", content_hash="same"),
        make_document(
            "空 格 重 复",
            source="C:/docs/a.docx",
        ),
        make_document(
            "空格重复",
            source="C:/docs/b.docx",
        ),
    ]

    result = ContextBuilder().build(documents)

    assert [item.content for item in result.items] == [
        "同一条 内容",
        "空 格 重 复",
    ]


def test_limits_chunks_per_logical_source() -> None:
    documents = [
        make_document(
            f"来源A内容{index}",
            source=f"C:/copies/a-{index}.docx",
            source_id="source-a",
        )
        for index in range(3)
    ]
    documents.append(
        make_document(
            "来源B内容",
            source="C:/docs/b.docx",
            source_id="source-b",
        )
    )

    result = ContextBuilder(
        max_chunks_per_source=2
    ).build(documents)

    assert [item.content for item in result.items] == [
        "来源A内容0",
        "来源A内容1",
        "来源B内容",
    ]


@pytest.mark.parametrize('configured_cap', [1, 4])
def test_single_known_source_keeps_late_conditions_within_text_budget(configured_cap):
    texts = ['培训总则。', '员工范围。', '申请流程。', '审批职责。',
             '课程记录。', '外包人员不适用上述培训办法。']
    documents = [make_document(text, source_id='training-policy', chunk_id=f't{i}')
                 for i, text in enumerate(texts)]
    with capture_trace() as trace:
        result = ContextBuilder(max_chunks_per_source=configured_cap).build(documents)
    assert [item.content for item in result.items] == texts
    assert [item.citation_id for item in result.items] == [f'资料{i}' for i in range(1, 7)]
    assert result.total_characters <= 12000
    selection = trace.stages['context_selection'][0]
    assert not selection['source_limit_applied']
    assert not any(row['reason'] == 'per_source_limit' for row in selection['exclusions'])


def test_single_source_still_obeys_character_budget_after_four_blocks():
    documents = [make_document(f'第{i}项条件。', source_id='policy', chunk_id=f'c{i}')
                 for i in range(6)]
    budget = ContextBuilder().build(documents[:5]).total_characters
    with capture_trace() as trace:
        result = ContextBuilder(max_characters=budget).build(documents)
    assert [item.chunk_id for item in result.items] == [f'c{i}' for i in range(5)]
    assert result.total_characters == budget == len(result.text)
    assert any(row['chunk_id'] == 'c5' and row['reason'] == 'character_budget_exhausted'
               for row in trace.stages['context_selection'][0]['exclusions'])


def test_single_source_does_not_truncate_an_overflowing_parent():
    documents = [make_document(f'完整条款{i}。', source_id='policy', chunk_id=f'p{i}')
                 for i in range(4)]
    overflowing = make_document('完整父块不能截断。' * 100, source_id='policy', chunk_id='long-parent')
    overflowing.metadata.update(chunk_type='parent', parent_id='parent-id')
    budget = ContextBuilder().build(documents).total_characters + 100
    with capture_trace() as trace:
        result = ContextBuilder(max_characters=budget).build(documents + [overflowing])
    assert len(result.items) == 4 and result.total_characters <= budget
    assert result.incomplete_reason == 'parent_context_budget_incomplete'
    assert any(row['chunk_id'] == 'long-parent' and row['reason'] == 'complete_parent_exceeds_remaining_budget'
               for row in trace.stages['context_selection'][0]['exclusions'])


def test_unknown_source_identity_does_not_disable_source_quota():
    documents = [Document(id=f'u{i}', page_content=f'独立内容{i}。') for i in range(6)]
    with capture_trace() as trace:
        result = ContextBuilder().build(documents)
    assert len(result.items) == 4
    assert trace.stages['context_selection'][0]['source_limit_applied']


def test_context_never_exceeds_character_budget() -> None:
    document = make_document(
        "前半部分内容" * 5 + "。" + "后续内容" * 100,
    )
    result = ContextBuilder(
        max_characters=80
    ).build([document])

    assert result.items
    assert result.total_characters <= 80
    assert len(result.text) <= 80
    assert result.items[0].content.endswith("。")


def test_too_small_budget_returns_empty_context() -> None:
    result = ContextBuilder(
        max_characters=5
    ).build([make_document("有效内容")])

    assert result == BuiltContext.empty()


def test_skips_long_later_block_and_keeps_shorter_block() -> None:
    first = make_document(
        "短内容A",
        source="C:/docs/a.docx",
    )
    long_second = make_document(
        "很长" * 100,
        source="C:/docs/b.docx",
    )
    short_third = make_document(
        "短内容C",
        source="C:/docs/c.docx",
    )
    one_block_length = len(
        ContextBuilder().build([first]).text
    )
    third_block_length = len(
        ContextBuilder().build([short_third]).text
    )
    budget = one_block_length + 2 + third_block_length

    result = ContextBuilder(
        max_characters=budget
    ).build([first, long_second, short_third])

    assert [item.content for item in result.items] == [
        "短内容A",
        "短内容C",
    ]


def test_missing_metadata_uses_safe_defaults() -> None:
    result = ContextBuilder().build([
        Document(page_content="无元数据内容")
    ])

    item = result.items[0]
    assert item.source == "未知来源"
    assert item.document_id is None
    assert item.document_name == "未知来源"
    assert item.section_path == "未标注章节"
    assert item.chunk_id is None
    assert item.rerank_score is None
