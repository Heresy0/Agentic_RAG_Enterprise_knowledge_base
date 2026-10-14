from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.exc import SQLAlchemyError

from test_answer_service import TEST_SCOPE, make_document, build_service, answer
from backend.app.evaluation.trace import capture_trace, record_documents, record_scope
from backend.app.knowledge.answer_models import GenerationAnswerDraft
from backend.app.knowledge.evidence_feedback import EvidenceFeedback
from backend.app.knowledge.rag_budget import current_rag_budget
from backend.app.knowledge.evidence_selection import select_evidence
from backend.app.security.authorization import AuthorizationDenied

QUESTION = "甲工艺与乙工艺的温度分别是多少？"


def missing(*, quote="乙工艺", reason="missing_evidence", ids=("Q1",)):
    return GenerationAnswerDraft.model_validate(dict(answerable=False, claims=[], refusal_reason="缺少乙工艺温度证据",
        coverage=[dict(requirement_id="Q1", aspect="乙工艺温度", status="insufficient", claim_indices=[], missing_information="缺温度")],
        evidence_gap=dict(reason=reason, requirement_ids=list(ids), source_quote=quote)))


def complete(*, temperature=90, citation="资料2"):
    return GenerationAnswerDraft.model_validate(dict(answerable=True, refusal_reason=None, evidence_gap=None,
        claims=[dict(text="甲工艺温度是80摄氏度", citations=["资料1"]),
                dict(text=f"乙工艺温度是{temperature}摄氏度", citations=[citation])],
        coverage=[dict(requirement_id="Q1", aspect="双方温度", status="answered", claim_indices=[1, 2], missing_information=None)]))


class Guard:
    available = True
    allowed = True
    def require(self):
        if not self.allowed:
            raise AuthorizationDenied("授权撤销")


def setup(monkeypatch, *, initial=None, candidate=None, extra=None, search_hook=None, model_hook=None, **limits):
    first = missing() if initial is None else initial
    candidate = complete() if candidate is None else candidate
    old = make_document("甲工艺温度是80摄氏度。", chunk_id="alpha")
    extra = [make_document("乙工艺温度是90摄氏度。", chunk_id="beta")] if extra is None else extra
    service, retrieval, generation, *_ = build_service(monkeypatch, model_result=first, documents=[old])
    service._settings.rag_feedback_enabled = True
    for key, value in limits.items():
        setattr(service._settings, key, value)
    guard, calls, removed = Guard(), [], set()
    def search(query, *, scope, session):
        retrieval.calls.append(dict(query=query, scope=scope, session=session))
        record_scope(query, scope)
        if search_hook:
            search_hook(guard, len(retrieval.calls))
        docs = [old] if len(retrieval.calls) == 1 else extra
        record_documents("rerank", docs)
        return select_evidence(query, docs)
    retrieval.search = search
    retrieval.refresh_evidence = lambda docs, **kwargs: [doc for doc in docs if doc.metadata.get("chunk_id", doc.id) not in removed]
    def factory(**kwargs):
        calls.append(kwargs)
        def invoke(messages):
            if model_hook:
                model_hook(guard, removed)
            if isinstance(candidate, Exception):
                raise candidate
            return dict(raw=AIMessage(content="", usage_metadata=dict(input_tokens=100, output_tokens=20, total_tokens=120)),
                        parsed=candidate, parsing_error=None)
        return SimpleNamespace(with_structured_output=lambda *args, **kwargs: SimpleNamespace(invoke=invoke))
    service._regeneration_model_factory = factory
    service._evidence_feedback = EvidenceFeedback(service._settings, retrieval, service._evidence_coordinator,
        service._context_builder, guard_factory=lambda *args: guard)
    return service, retrieval, generation, calls, guard, removed


def execute(service):
    with capture_trace() as trace:
        result = answer(service, QUESTION)
    return result, trace


def budget(trace):
    return next(row for row in trace.stages["rag_routing"] if row["kind"] == "budget")


def outcome(trace):
    return [row["outcome"] for row in trace.stages["rag_routing"] if row["kind"] == "feedback"][-1]


def test_one_directed_search_and_regeneration_use_original_question_and_final_validator(monkeypatch):
    service, retrieval, first, calls, _, _ = setup(monkeypatch)
    result, trace = execute(service)
    assert result.answerable and "80" in result.answer and "90" in result.answer
    assert len(retrieval.calls) == 2 and len(first.calls) == len(calls) == 1
    assert "乙工艺" in retrieval.calls[1]["query"] and QUESTION in retrieval.calls[1]["query"]
    assert all(row["scope"] == TEST_SCOPE for row in retrieval.calls)
    assert trace.query == QUESTION
    assert budget(trace)["calls"] == budget(trace)["feedback_attempts"] == budget(trace)["regenerations"] == 1
    assert budget(trace)["charged_tokens"] == 120
    assert trace.stages["answer_generation"][0]["response"]["answerable"] is False
    assert trace.stages["answer_regeneration"][0]["response"]["answerable"] is True
    assert len(trace.stages["initial_context"]) == 1 and len(trace.stages["context"]) == 2
    assert len(trace.stages["feedback_retrieval"]) == 1
    assert [row["stage"] for row in trace.stages["model_usage"]] == ["answer_generation", "answer_regeneration"]
    assert result.timings.feedback_search_ms is not None and result.timings.answer_regeneration_ms is not None
    assert calls[0]["max_retries"] == 0 and calls[0]["max_tokens"] == 2048 and calls[0]["timeout"] <= 15


@pytest.mark.parametrize("initial", [missing(reason="conflict", quote=None),
    missing(reason="unsupported_request", quote=None), missing(quote="其他租户"), missing(ids=("Q9",)),
    missing(ids=("Q1", "Q1")),
    GenerationAnswerDraft.model_validate(dict(answerable=False, claims=[], refusal_reason="缺证据",
        coverage=[dict(requirement_id="Q1", aspect="温度", status="insufficient", claim_indices=[], missing_information="缺温度")])),
])
def test_only_well_formed_anchored_evidence_gaps_are_retried(monkeypatch, initial):
    service, retrieval, first, calls, _, _ = setup(monkeypatch, initial=initial)
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 1 and not calls
    assert budget(trace)["feedback_attempts"] == budget(trace)["regenerations"] == 0


def test_coverage_format_error_is_not_treated_as_missing_evidence(monkeypatch):
    initial = missing()
    initial.coverage[0].requirement_id = "Q9"
    service, retrieval, first, calls, _, _ = setup(monkeypatch, initial=initial)
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 1 and not calls and outcome(trace) == "ineligible"


@pytest.mark.parametrize("extra", [[], [make_document("甲工艺温度是80摄氏度。", chunk_id="copy")]])
def test_no_new_usable_content_does_not_spend_regeneration_call(monkeypatch, extra):
    service, retrieval, first, calls, _, _ = setup(monkeypatch, extra=extra)
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 2 and not calls
    assert outcome(trace) == "no_new_evidence" and budget(trace)["feedback_attempts"] == 1
    assert budget(trace)["calls"] == budget(trace)["regenerations"] == 0


def test_foreign_scope_evidence_never_enters_regeneration(monkeypatch):
    foreign = make_document("FOREIGN SECRET 90摄氏度", chunk_id="foreign")
    foreign.metadata["tenant_id"] = "another-tenant"
    service, retrieval, first, calls, _, _ = setup(monkeypatch, extra=[foreign])
    result, trace = execute(service)
    assert not result.answerable and not calls and "FOREIGN SECRET" not in str(trace.to_dict())


@pytest.mark.parametrize("limits", [dict(rag_max_optional_calls=0), dict(rag_max_optional_tokens=1),
    dict(rag_max_optional_seconds=0), dict(rag_max_searches=1)])
def test_budget_exhaustion_prevents_feedback_search(monkeypatch, limits):
    service, retrieval, first, calls, _, _ = setup(monkeypatch, **limits)
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 1 and not calls
    assert outcome(trace) == "shared_budget"


def test_query_already_used_by_agent_is_not_repeated(monkeypatch):
    def hook(guard, count):
        if count == 1:
            current_rag_budget().note_query("乙工艺")
    service, retrieval, first, calls, _, _ = setup(monkeypatch, search_hook=hook)
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 1 and not calls and outcome(trace) == "repeated_query"


def test_whole_question_quote_is_not_a_directed_retry(monkeypatch):
    service, retrieval, first, calls, _, _ = setup(monkeypatch, initial=missing(quote=QUESTION))
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 1 and not calls and outcome(trace) == "repeated_query"


def test_earlier_decision_call_consumes_shared_regeneration_slot(monkeypatch):
    def hook(guard, count):
        if count == 1:
            with current_rag_budget().model_attempt(prompt_bytes=10, output_tokens=10):
                pass
    service, retrieval, first, calls, _, _ = setup(monkeypatch, search_hook=hook, rag_max_optional_calls=1)
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 1 and not calls
    assert budget(trace)["calls"] == 1 and budget(trace)["regenerations"] == 0


def test_enlarged_prompt_is_rechecked_before_regeneration(monkeypatch):
    from backend.app.knowledge.rag_budget import RagBudget, rag_budget_scope
    large = make_document("乙工艺温度是90摄氏度。" + "说明文字" * 1500, chunk_id="large")
    service, retrieval, first, calls, _, _ = setup(monkeypatch, extra=[large])
    old_context = service._context_builder.build(select_evidence(QUESTION, retrieval.documents), question=QUESTION)
    with rag_budget_scope(RagBudget()):
        _, prompt_bytes, output = service._regeneration_preflight(QUESTION, old_context)
    service._settings.rag_max_optional_tokens = prompt_bytes + output
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 2 and not calls
    assert outcome(trace) == "shared_budget" and budget(trace)["regenerations"] == 0


def test_second_decline_never_starts_another_feedback_round(monkeypatch):
    service, retrieval, first, calls, _, _ = setup(monkeypatch, candidate=missing())
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 2 and len(calls) == 1
    assert budget(trace)["feedback_attempts"] == budget(trace)["regenerations"] == 1
    assert trace.stages["answer_regeneration"][0]["response"]["answerable"] is False


@pytest.mark.parametrize("candidate", [complete(temperature=95), complete(citation="资料999")])
def test_regenerated_answer_still_must_pass_citation_and_numeric_validation(monkeypatch, candidate):
    service, retrieval, first, calls, _, _ = setup(monkeypatch, candidate=candidate)
    result, trace = execute(service)
    assert not result.answerable and not result.citations and len(calls) == 1
    assert trace.stages["answer_generation"][0]["response"]["answerable"] is False


@pytest.mark.parametrize("phase", ["search", "regeneration"])
def test_revocation_during_feedback_is_terminal(monkeypatch, phase):
    search_hook = lambda guard, count: setattr(guard, "allowed", False) if count == 2 and phase == "search" else None
    model_hook = lambda guard, removed: setattr(guard, "allowed", False) if phase == "regeneration" else None
    service, retrieval, first, calls, _, _ = setup(monkeypatch, search_hook=search_hook, model_hook=model_hook)
    with pytest.raises(AuthorizationDenied):
        execute(service)
    assert len(calls) == (1 if phase == "regeneration" else 0)


def test_document_change_during_regeneration_discards_candidate(monkeypatch):
    service, retrieval, first, calls, _, _ = setup(monkeypatch, model_hook=lambda guard, removed: removed.add("alpha"))
    result, trace = execute(service)
    assert not result.answerable and not result.citations and len(calls) == 1
    assert outcome(trace) == "evidence_changed_after_generation"


def test_regeneration_failure_is_charged_without_retry(monkeypatch):
    service, retrieval, first, calls, _, _ = setup(monkeypatch, candidate=TimeoutError())
    result, trace = execute(service)
    assert not result.answerable and len(calls) == 1 and budget(trace)["regenerations"] == 1
    assert budget(trace)["charged_tokens"] > 0 and outcome(trace) == "fallback"
    assert trace.stages["model_usage"][-1]["stage"] == "answer_regeneration"
    assert trace.stages["model_usage"][-1]["total_tokens"] is None


def test_database_error_does_not_return_stale_evidence(monkeypatch):
    service, retrieval, first, calls, _, _ = setup(monkeypatch)
    def refresh(*args, **kwargs):
        raise SQLAlchemyError("database unavailable")
    retrieval.refresh_evidence = refresh
    with pytest.raises(SQLAlchemyError):
        execute(service)
    assert not calls


def test_disabled_feedback_preserves_single_pass(monkeypatch):
    service, retrieval, first, calls, _, _ = setup(monkeypatch)
    service._settings.rag_feedback_enabled = False
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 1 and not calls
    assert "feedback_search_ms" not in result.timings.model_dump()


def test_positive_draft_cannot_hide_its_declared_conflict(monkeypatch):
    candidate = complete()
    candidate.evidence_gap = missing(reason="conflict", quote=None).evidence_gap
    service, retrieval, first, calls, _, _ = setup(monkeypatch, candidate=candidate)
    result, trace = execute(service)
    assert not result.answerable and "证据缺口" in result.refusal_reason


def test_document_retired_during_search_stops_before_regeneration(monkeypatch):
    service, retrieval, first, calls, _, removed = setup(monkeypatch)
    original_search = retrieval.search
    def search(*args, **kwargs):
        result = original_search(*args, **kwargs)
        if len(retrieval.calls) == 2:
            removed.add("alpha")
        return result
    retrieval.search = search
    result, trace = execute(service)
    assert not result.answerable and not calls and outcome(trace) == "evidence_changed_after_search"


def test_failed_embedding_in_feedback_is_preserved_once_in_parent_usage_trace(monkeypatch):
    from backend.app.model_usage import ModelCall
    service, retrieval, first, calls, _, _ = setup(monkeypatch)
    original_search = retrieval.search
    def search(*args, **kwargs):
        if len(retrieval.calls) == 1:
            with ModelCall('query_embedding', 'fake') as call:
                call.observe(dict(total_tokens=7))
                raise TimeoutError()
        return original_search(*args, **kwargs)
    retrieval.search = search
    result, trace = execute(service)
    assert not result.answerable and not calls and outcome(trace) == 'fallback'
    usage = trace.stages['model_usage']
    assert [row['stage'] for row in usage] == ['answer_generation', 'query_embedding']
    assert usage[-1]['total_tokens'] == 7 and usage[-1]['status'] == 'error'
    assert len(trace.stages['feedback_retrieval']) == 1


def test_unused_blank_quote_does_not_turn_conflict_into_retrieval(monkeypatch):
    initial = missing(reason='conflict', quote='')
    assert initial.evidence_gap.source_quote is None
    service, retrieval, first, calls, _, _ = setup(monkeypatch, initial=initial)
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 1 and not calls


def test_empty_answered_row_in_overall_refusal_is_withheld_only_for_feedback(monkeypatch):
    from backend.app.knowledge.answer_models import AnswerCoverage
    initial = missing()
    initial.coverage.insert(0, AnswerCoverage(requirement_id='Q1', aspect='甲工艺温度', status='answered',
        claim_indices=[], missing_information=None))
    service, retrieval, first, calls, _, _ = setup(monkeypatch, initial=initial)
    result, trace = execute(service)
    assert result.answerable and len(retrieval.calls) == 2 and len(calls) == 1
    assert initial.coverage[0].status == 'answered'
    assert trace.stages['answer_generation'][0]['coverage']['errors']
    assert any(row.get('kind') == 'feedback_normalization' and row['withheld_rows'] == 1
               for row in trace.stages['rag_routing'])


def test_nonexistent_claim_binding_is_not_normalized_to_enable_retrieval(monkeypatch):
    from backend.app.knowledge.answer_models import AnswerCoverage
    initial = missing()
    initial.coverage.insert(0, AnswerCoverage(requirement_id='Q1', aspect='甲工艺温度', status='answered',
        claim_indices=[1], missing_information=None))
    service, retrieval, first, calls, _, _ = setup(monkeypatch, initial=initial)
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 1 and not calls


def test_normalization_cannot_invent_an_insufficient_requirement(monkeypatch):
    initial = missing()
    initial.coverage[0].status = 'answered'
    initial.coverage[0].missing_information = None
    service, retrieval, first, calls, _, _ = setup(monkeypatch, initial=initial)
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 1 and not calls


def test_verified_partial_claim_in_overall_refusal_is_never_reused(monkeypatch):
    from backend.app.knowledge.answer_models import AnswerClaim, AnswerCoverage
    initial = missing()
    initial.claims = [AnswerClaim(text='甲工艺温度是80摄氏度', citations=['资料1'])]
    initial.coverage.insert(0, AnswerCoverage(requirement_id='Q1', aspect='甲工艺温度', status='answered',
        claim_indices=[1], missing_information='已从资料查到'))
    service, retrieval, first, calls, _, _ = setup(monkeypatch, initial=initial)
    result, trace = execute(service)
    assert result.answerable and len(retrieval.calls) == 2 and len(calls) == 1
    assert len(initial.claims) == 1 and initial.coverage[0].status == 'answered'
    assert trace.stages['answer_generation'][0]['response']['answerable'] is False
    assert trace.stages['answer_generation'][0]['claims'][0]['text'] == initial.claims[0].text


def test_unsupported_partial_claim_cannot_be_discarded_to_start_feedback(monkeypatch):
    from backend.app.knowledge.answer_models import AnswerClaim, AnswerCoverage
    initial = missing()
    initial.claims = [AnswerClaim(text='甲工艺温度是95摄氏度', citations=['资料1'])]
    initial.coverage.insert(0, AnswerCoverage(requirement_id='Q1', aspect='甲工艺温度', status='answered',
        claim_indices=[1], missing_information=None))
    service, retrieval, first, calls, _, _ = setup(monkeypatch, initial=initial)
    result, trace = execute(service)
    assert not result.answerable and len(retrieval.calls) == 1 and not calls
