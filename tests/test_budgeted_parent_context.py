"""Context overflow regressions: literal sources, conditions and safe fallback."""
from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.documents import Document
from langchain_core.messages import AIMessage
import pytest

from backend.app.evaluation.reporting import quality_layers
from backend.app.evaluation.runner import score_trace
from backend.app.evaluation.trace import capture_trace, record_context, record_scope
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.parent_context_selection import (
    BudgetedParentSelector, SectionSelection, parent_selection_scope,
)
from backend.app.knowledge.query_planner import QueryPlan, query_plan_scope
from backend.app.knowledge.rag_budget import RagBudget, rag_budget_scope
from backend.app.model_usage import usage_context
from backend.app.security.authorization import AuthorizationDenied
from backend.app.security.retrieval_scope import RetrievalScope


SCOPE = RetrievalScope(uuid4(), uuid4())
QUESTION = "设备巡检周期是多少？"


def parent(condition="仅在客户书面同意后方可执行。", *, chunk_id="child"):
    child = "设备巡检周期为30天。"
    content = "\n\n".join([
        "## 巡检周期\n\n" + child,
        "## 补充说明\n\n" + condition,
        "## 活动记录\n\n" + "园区活动使用绿色标志。" * 60,
    ])
    return Document(id=chunk_id, page_content=content, metadata=dict(
        tenant_id=str(SCOPE.tenant_id), knowledge_base_id=str(SCOPE.knowledge_base_id),
        document_id=str(uuid4()), source="inspection.md", document_name="巡检规范",
        section_path="巡检周期", chunk_type="parent", parent_id="p-" + chunk_id,
        vector_generation="g", matched_child_section="巡检周期", matched_child_content=child,
    ))


class Guard:
    available = True
    allowed = True

    def require(self):
        if not self.allowed:
            raise AuthorizationDenied("授权已撤销")


def selector(result=None, *, hook=None, guard=None, **limits):
    result = result if result is not None else dict(use_full_parent=False, keep_ids=["S1"])
    settings = SimpleNamespace(chat_model="test", chat_base_url="https://example.invalid/v1",
                               chat_api_key="test", **limits)
    calls = []
    guard = guard or Guard()

    def factory(**kwargs):
        def invoke(messages):
            calls.append(dict(config=kwargs, messages=messages))
            if hook:
                hook()
            if isinstance(result, Exception):
                raise result
            return dict(parsed=result, raw=AIMessage(content="", usage_metadata=dict(
                input_tokens=100, output_tokens=20, total_tokens=120)), parsing_error=None)
        return SimpleNamespace(with_structured_output=lambda schema, **kw:
                               SimpleNamespace(invoke=invoke) if schema is SectionSelection else None)

    return BudgetedParentSelector(settings, model_factory=factory, guard_factory=lambda *args: guard), calls


@contextmanager
def request(*, budget=None):
    budget = budget or RagBudget()
    with rag_budget_scope(budget), parent_selection_scope(scope=SCOPE, session=object()):
        yield budget


def builder_for(document, selected, *, content_budget=180):
    full = ContextBuilder().build([document])
    prefix_length = full.total_characters - len(document.page_content.strip())
    return ContextBuilder(max_characters=prefix_length + content_budget, parent_selector=selected)


@pytest.mark.parametrize("exact_budget", [False, True])
def test_full_parent_with_sufficient_budget_never_calls_selection_model(exact_budget):
    document = parent()
    selected, calls = selector()
    maximum = ContextBuilder().build([document]).total_characters if exact_budget else 12000
    with request(), capture_trace() as trace:
        context = ContextBuilder(max_characters=maximum, parent_selector=selected).build([document], question=QUESTION)
    assert context.items[0].content == document.page_content and not context.incomplete_reason
    assert not calls
    assert trace.stages["context_selection"][0]["parent_selections"][0]["reason"] == "full_parent_default"


@pytest.mark.parametrize("condition", [
    "仅在客户书面同意后方可执行。",
    "除非进入停机窗口，否则保持原安排。",
    "节假日除外。",
    "客户完成签字，流程方可启动。",
    "四项指标同时成立才算通过。",
    "遇到故障，恢复供电后执行。",
    "Only if written consent is obtained. Except on public holidays.",
    "1. 取得签字\n2. 完成登记\n3. 提交附件",
    "等级|频次|前置事项\nA|30天|书面确认",
])
def test_model_cannot_remove_conditions_exceptions_lists_or_tables(condition):
    document = parent(condition)
    selected, calls = selector()  # A flawed model omits every supporting condition.
    builder = builder_for(document, selected, content_budget=240)
    with request(), capture_trace() as trace:
        context = builder.build([document], question=QUESTION)
    assert len(calls) == 1 and not context.incomplete_reason
    assert document.metadata["matched_child_content"] in context.items[0].content
    assert condition in context.items[0].content and "绿色标志" not in context.items[0].content
    assert context.total_characters <= builder.max_characters
    assert context.items[0].chunk_id == document.id and context.items[0].parent_id == document.metadata["parent_id"]
    decision = trace.stages["context_selection"][0]["parent_selections"][0]
    assert decision["restored_ids"] == ["S2"]
    for start, end in decision["spans"]:
        assert document.page_content[start:end].strip() in context.items[0].content


def test_model_can_keep_semantically_related_prose_without_keyword_overlap():
    document = parent("客户已签字，审批正在流转。")
    selected, calls = selector(dict(use_full_parent=False, keep_ids=["S2", "S1"]))
    with request():
        context = builder_for(document, selected).build([document], question=QUESTION)
    assert calls and "客户已签字，审批正在流转。" in context.items[0].content
    assert context.items[0].content.index("巡检周期") < context.items[0].content.index("补充说明")


def test_protected_text_that_does_not_fit_is_never_truncated_or_sent_for_compression():
    document = parent("仅在客户书面同意后方可执行。" * 20)
    selected, calls = selector()
    with request(), capture_trace() as trace:
        context = builder_for(document, selected, content_budget=60).build([document], question=QUESTION)
    assert not context.items and context.incomplete_reason == "parent_context_budget_incomplete"
    assert not calls
    assert trace.stages["context_selection"][0]["parent_selections"][0]["reason"] == "protected_sections_exceed_budget"


@pytest.mark.parametrize("result", [
    TimeoutError("provider timeout"),
    dict(use_full_parent=True, keep_ids=[]),
    dict(use_full_parent=False, keep_ids=["invented"]),
    dict(use_full_parent=False, keep_ids=["S1", "S1"]),
    dict(use_full_parent=False, keep_ids=["S1", "S2", "S3"]),
    dict(use_full_parent=False, keep_ids=[]),
    dict(use_full_parent=False, keep_ids=["S1"], rewritten_text="没有限制"),
    dict(use_full_parent="false", keep_ids=["S1"]),
])
def test_failed_uncertain_or_invalid_selection_falls_back_without_truncating_parent(result):
    document = parent()
    selected, calls = selector(result)
    with request():
        context = builder_for(document, selected).build([document], question=QUESTION)
    assert len(calls) == 1 and context.incomplete_reason and not context.items


def test_complex_questions_and_cross_references_keep_whole_parent():
    for text, question, plan in [
        ("参见第三条的限制。", QUESTION, None),
        ("普通说明。", "两种设备的巡检周期分别是多少？", None),
        ("普通说明。", QUESTION, QueryPlan(intent="multi_part", strategy="agentic", reason="dependent_lookup")),
    ]:
        document = parent(text)
        selected, calls = selector()
        with request(), query_plan_scope(plan):
            context = builder_for(document, selected).build([document], question=question)
        assert not calls and context.incomplete_reason and not context.items


def test_refresh_reuses_selected_spans_and_one_call_limit_survives_changed_evidence():
    document = parent()
    selected, calls = selector()
    builder = builder_for(document, selected)
    with request(), usage_context(scope=SCOPE) as usage:
        first = builder.build([document], question=QUESTION)
        refreshed = builder.build([deepcopy(document)], question=QUESTION)
        assert refreshed == first and len(calls) == 1
        changed = deepcopy(document)
        changed.page_content += "\n\n## 附录\n\n另一项原文。"
        assert builder.build([changed], question=QUESTION).incomplete_reason
        assert len(calls) == 1
    assert usage.events[0]["stage"] == "context_selection" and usage.events[0]["total_tokens"] == 120
    with request():
        assert builder.build([document], question=QUESTION) == first
    assert len(calls) == 2  # No cache across users/requests.


@pytest.mark.parametrize("mode", ["disabled", "calls", "tokens", "time", "prompt", "identity"])
def test_selection_respects_configuration_identity_and_shared_budget(mode):
    document = parent()
    budget = RagBudget()
    guard = Guard()
    limits = {}
    if mode == "disabled": limits["rag_context_selection_enabled"] = False
    if mode == "calls": budget.calls = budget.max_calls
    if mode == "tokens": budget.charged_tokens = budget.max_tokens
    if mode == "time": budget.seconds = budget.max_seconds
    if mode == "prompt": limits["rag_context_selection_prompt_bytes"] = 10
    if mode == "identity": guard.available = False
    selected, calls = selector(guard=guard, **limits)
    with request(budget=budget):
        context = builder_for(document, selected).build([document], question=QUESTION)
    assert not calls and context.incomplete_reason


def test_scope_and_revoked_identity_fail_closed():
    document = parent()
    guard = Guard()
    selected, calls = selector(guard=guard, hook=lambda: setattr(guard, "allowed", False))
    with request(), pytest.raises(AuthorizationDenied):
        builder_for(document, selected).build([document], question=QUESTION)
    assert len(calls) == 1
    document.metadata["knowledge_base_id"] = str(uuid4())
    selected, calls = selector()
    with request(), pytest.raises(AuthorizationDenied):
        builder_for(document, selected).build([document], question=QUESTION)
    assert not calls


def test_later_parent_uses_remaining_budget_and_retains_all_constraints():
    first = Document(id="first", page_content="设备登记信息。", metadata=dict(source="register.md"))
    document = parent()
    selected, calls = selector()
    parent_budget = builder_for(document, selected).max_characters
    builder = ContextBuilder(max_characters=ContextBuilder().build([first]).total_characters + 2 + parent_budget,
                             parent_selector=selected)
    with request():
        context = builder.build([first, document], question=QUESTION)
    assert len(calls) == 1 and not context.incomplete_reason
    assert [item.chunk_id for item in context.items] == ["first", "child"]
    assert "书面同意" in context.items[1].content


@pytest.mark.parametrize("mutation", ["missing_child", "wrong_section", "one_section"])
def test_unverifiable_sections_fall_back_to_full_without_model_call(mutation):
    document = parent()
    if mutation == "missing_child": document.metadata.pop("matched_child_content")
    if mutation == "wrong_section": document.metadata["matched_child_section"] = "未知章节"
    if mutation == "one_section": document.page_content = document.page_content.replace("## 补充说明", "补充说明").replace("## 活动记录", "活动记录")
    selected, calls = selector()
    with request():
        context = builder_for(document, selected).build([document], question=QUESTION)
    assert not calls and context.incomplete_reason and not context.items


def test_two_overflowing_parents_do_not_create_a_model_call_per_parent():
    first, second = parent(), parent("必须经过负责人同意。", chunk_id="second")
    selected, calls = selector()
    with request():
        assert len(selected.select(first, question=QUESTION, max_characters=180).page_content) <= 180
        unchanged = selected.select(second, question=QUESTION, max_characters=180)
    assert len(calls) == 1 and unchanged.page_content == second.page_content
    assert unchanged.metadata["parent_selection"]["reason"] == "selection_attempt_limit"


def test_answer_service_uses_selection_and_generation_in_the_same_request(monkeypatch):
    from test_answer_service import build_service, answer, TEST_SCOPE
    from backend.app.knowledge.answer_models import AnswerDraft, AnswerClaim
    document = parent()
    document.metadata.update(tenant_id=str(TEST_SCOPE.tenant_id), knowledge_base_id=str(TEST_SCOPE.knowledge_base_id))
    original = deepcopy(document)
    selected, calls = selector()
    service, _, model, *_ = build_service(monkeypatch, documents=[document], model_result=AnswerDraft(
        answerable=True, claims=[AnswerClaim(text="设备巡检周期为30天，仅在客户书面同意后方可执行。", citations=["资料1"]) ]))
    assert isinstance(service._context_builder.parent_selector, BudgetedParentSelector)
    service._context_builder = builder_for(document, selected)
    with capture_trace() as trace:
        result = answer(service, QUESTION)
    assert result.answerable and "书面同意" in result.answer
    assert len(calls) == len(model.calls) == 1
    assert "书面同意" in model.calls[0][1].content and "绿色标志" not in model.calls[0][1].content
    assert result.citations[0].chunk_id == document.id and result.citations[0].parent_id == document.metadata["parent_id"]
    assert document == original
    assert trace.stages["rag_routing"][-1]["calls"] == 1


def test_unresolved_parent_budget_refuses_before_answer_generation(monkeypatch):
    from test_answer_service import build_service, answer, TEST_SCOPE
    from backend.app.knowledge.answer_models import AnswerDraft
    document = parent()
    document.metadata.update(tenant_id=str(TEST_SCOPE.tenant_id), knowledge_base_id=str(TEST_SCOPE.knowledge_base_id))
    service, _, model, *_ = build_service(monkeypatch, model_result=AnswerDraft(answerable=False), documents=[document])
    service._context_builder = ContextBuilder(max_characters=120)
    with capture_trace() as trace:
        result = answer(service, QUESTION)
    assert not result.answerable and not model.calls and "预算不足" in result.refusal_reason
    assert trace.stages["answer_decisions"][-1]["reason"] == "parent_context_budget_incomplete"
    assert trace.stages["context_status"][0]["incomplete_reason"]


def test_context_evaluation_rejects_budget_gap_even_if_labeled_anchor_is_present():
    document = parent()
    case = dict(id="budget", question=QUESTION, answerable=True,
                expected_evidence=[dict(document="巡检规范", text="设备巡检周期为30天。")])
    with capture_trace() as trace:
        record_scope(QUESTION, SCOPE)
        context = ContextBuilder().build([document])
        context = type(context)(context.text, context.items, context.total_characters, "parent_context_budget_incomplete")
        record_context(context)
    record = score_trace(case, trace.to_dict())
    layers = quality_layers(dict(records=[record]))
    assert record["metrics"]["context"]["all_evidence_present"]
    assert record["status"] == "failed"
    assert layers["context"]["summary"]["failed"] == 1
    assert layers["ranking"]["summary"]["failed"] == 1
