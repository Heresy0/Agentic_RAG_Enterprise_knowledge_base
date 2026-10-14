from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from test_answer_service import TEST_SCOPE, make_document, build_service, answer
from backend.app.evaluation.trace import capture_trace
from backend.app.knowledge.answer_models import GenerationAnswerDraft
from backend.app.knowledge.answer_repair import AnswerRepair
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.rag_budget import RagBudget, rag_budget_scope
from backend.app.model_usage import usage_context
from backend.app.security.authorization import AuthorizationDenied


class Guard:
    available = True
    allowed = True
    def require(self):
        if not self.allowed:
            raise AuthorizationDenied("撤权")


def draft(*, text="温度是80摄氏度", citation="资料1", indices=(2,), status="answered", answerable=True):
    return GenerationAnswerDraft.model_validate(dict(answerable=answerable,
        claims=[dict(text=text, citations=[citation])], refusal_reason=None,
        coverage=[dict(requirement_id="Q1", aspect="温度", status=status, claim_indices=list(indices),
                       missing_information=None if status == "answered" else "缺少温度证据")]))


def repair_service(result, *, hook=None):
    calls, guard = [], Guard()
    def factory(**kwargs):
        calls.append(kwargs)
        def invoke(messages):
            if hook:
                hook(guard)
            if isinstance(result, Exception):
                raise result
            return dict(raw=AIMessage(content="", usage_metadata=dict(input_tokens=100, output_tokens=20, total_tokens=120)),
                        parsed=result, parsing_error=None)
        return SimpleNamespace(with_structured_output=lambda *args, **kwargs: SimpleNamespace(invoke=invoke))
    settings = SimpleNamespace(rag_repair_enabled=True, chat_model="fake", chat_base_url="https://fake.invalid", chat_api_key="fake")
    return AnswerRepair(settings, model_factory=factory, guard_factory=lambda *args: guard), calls, guard


def execute(service, original, *, refresh=lambda: True, budget=None, context=None):
    context = context or ContextBuilder().build([make_document("温度是80摄氏度。")], question="温度是多少？")
    with usage_context(scope=TEST_SCOPE) as usage, rag_budget_scope(budget or RagBudget()), capture_trace() as trace:
        result = service.repair(original, question="温度是多少？", context=context,
            validator=AnswerValidator(), scope=TEST_SCOPE, session=object(), refresh_context=refresh)
    return result, usage, trace


def test_one_model_repair_fixes_coverage_without_changing_facts():
    original, fixed = draft(), draft(indices=(1,))
    service, calls, _ = repair_service(fixed)
    result, usage, trace = execute(service, original)
    assert result == fixed and original.coverage[0].claim_indices == [2]
    assert len(calls) == 1 and calls[0]["max_retries"] == 0
    assert usage.events[0]["stage"] == "answer_repair" and usage.events[0]["total_tokens"] == 120
    assert trace.stages["rag_routing"][-1]["outcome"] == "model_repaired"


def test_unambiguous_citation_format_is_repaired_locally():
    original = draft(citation=" [资料1] ", indices=(1,))
    service, calls, _ = repair_service(RuntimeError("must not call"))
    result, usage, trace = execute(service, original)
    assert result.claims[0].citations == ["资料1"] and not calls and not usage.events
    assert trace.stages["rag_routing"][-1]["outcome"] == "local_repaired"


@pytest.mark.parametrize("original", [
    draft(text="温度是90摄氏度", indices=(1,)),
    draft(status="insufficient", indices=()),
    GenerationAnswerDraft.model_validate(dict(answerable=False, claims=[], refusal_reason="缺证据",
        coverage=[dict(requirement_id="Q1", aspect="温度", status="insufficient", claim_indices=[], missing_information="缺证据")])),
])
def test_missing_truth_and_unsupported_numbers_never_trigger_repair(original):
    service, calls, _ = repair_service(draft(indices=(1,)))
    result, usage, trace = execute(service, original)
    assert result == original and not calls and not usage.events


@pytest.mark.parametrize("candidate", [
    draft(text="温度是90摄氏度", indices=(1,)),
    draft(text="温度是80摄氏度，且无需审核", indices=(1,)),
    draft(indices=(2,)),
    draft(citation="资料999", indices=(1,)),
])
def test_repair_cannot_rewrite_facts_or_bypass_final_validator(candidate):
    original = draft()
    service, calls, _ = repair_service(candidate)
    result, usage, trace = execute(service, original)
    assert result == original and len(calls) == 1
    assert trace.stages["rag_routing"][-1]["outcome"] == "rejected"


def test_changed_evidence_stops_repair_before_model_call():
    service, calls, _ = repair_service(draft(indices=(1,)))
    original = draft()
    result, _, trace = execute(service, original, refresh=lambda: False)
    assert result == original and not calls
    assert trace.stages["rag_routing"][-1]["outcome"] == "evidence_changed"


def test_evidence_change_after_model_discards_repair():
    service, calls, _ = repair_service(draft(indices=(1,)))
    checks = iter([True, False])
    original = draft()
    result, _, trace = execute(service, original, refresh=lambda: next(checks))
    assert result == original and len(calls) == 1
    assert trace.stages["rag_routing"][-1]["outcome"] == "rejected"


def test_revocation_after_model_is_not_silent_fallback():
    service, calls, _ = repair_service(draft(indices=(1,)), hook=lambda guard: setattr(guard, "allowed", False))
    with pytest.raises(AuthorizationDenied):
        execute(service, draft())
    assert len(calls) == 1


def test_exhausted_shared_budget_blocks_model_repair():
    service, calls, _ = repair_service(draft(indices=(1,)))
    original = draft()
    result, _, trace = execute(service, original, budget=RagBudget(max_calls=0))
    assert result == original and not calls


def test_provider_failure_is_only_one_attempt_and_keeps_original_draft():
    service, calls, _ = repair_service(TimeoutError())
    budget = RagBudget()
    original = draft()
    result, usage, trace = execute(service, original, budget=budget)
    assert result == original and len(calls) == budget.calls == 1
    assert budget.charged_tokens > 0 and usage.events[0]["total_tokens"] is None


def test_citation_normalization_cannot_hide_unsupported_number():
    original = draft(text="温度是90摄氏度", citation="[资料1]", indices=(1,))
    service, calls, _ = repair_service(draft(indices=(1,)))
    result, _, trace = execute(service, original)
    assert result == original and not calls
    assert trace.stages["rag_routing"][-1]["outcome"] == "unsupported_after_normalization"


def test_large_repair_prompt_is_not_truncated_or_sent():
    service, calls, _ = repair_service(draft(indices=(1,)))
    service.settings.rag_repair_prompt_bytes = 1
    original = draft()
    result, _, trace = execute(service, original)
    assert result == original and not calls
    assert trace.stages["rag_routing"][-1]["outcome"] == "prompt_budget"


def test_answer_pipeline_repair_preserves_first_pass_failure_trace(monkeypatch):
    original, fixed = draft(), draft(indices=(1,))
    service, retrieval, generation, *_ = build_service(monkeypatch, model_result=original,
                                                       documents=[make_document("温度是80摄氏度。")])
    service._settings.rag_planner_mode = "auto"
    service._settings.rag_repair_enabled = True
    service._answer_repair, calls, _ = repair_service(fixed)
    retrieval.refresh_evidence = lambda docs, **kwargs: docs
    with capture_trace() as trace:
        result = answer(service, "温度是多少？")
    assert result.answerable and "80" in result.answer
    assert len(generation.calls) == 1 and len(calls) == 1 and len(retrieval.calls) == 1
    assert trace.stages["answer_generation"][0]["response"]["answerable"] is False
    assert trace.stages["answer_generation"][0]["coverage"]["errors"]
    assert result.timings.query_planning_ms is not None and result.timings.answer_repair_ms is not None
