from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.knowledge.rag_budget import RagBudget, RagBudgetExceeded, current_rag_budget, rag_budget_scope
from backend.app.model_usage import usage_context
from backend.app.security.retrieval_scope import RetrievalScope


def test_failed_unknown_attempt_consumes_budget_and_cannot_retry_for_free():
    budget = RagBudget(max_calls=1)
    with pytest.raises(TimeoutError), budget.model_attempt(prompt_bytes=100, output_tokens=20):
        raise TimeoutError()
    assert budget.calls == 1 and budget.charged_tokens == 120
    with pytest.raises(RagBudgetExceeded), budget.model_attempt(prompt_bytes=100, output_tokens=20):
        pytest.fail("must not execute")


def test_reported_zero_is_known_and_mixed_unknown_is_reserved():
    scope = RetrievalScope(uuid4(), uuid4())
    budget = RagBudget()
    with usage_context(scope=scope) as usage:
        with budget.model_attempt(prompt_bytes=100, output_tokens=20):
            usage.events.append(dict(total_tokens=0))
        assert budget.charged_tokens == 0
        with budget.model_attempt(prompt_bytes=100, output_tokens=20):
            usage.events.extend([dict(total_tokens=20), dict(total_tokens=None)])
        assert budget.charged_tokens == 120


def test_tools_and_three_strategies_share_one_request_budget():
    budget = RagBudget(max_calls=2, max_reads=1, max_searches=2)
    with rag_budget_scope(budget), ThreadPoolExecutor(max_workers=1) as executor:
        with current_rag_budget().model_attempt(prompt_bytes=10, output_tokens=10):
            pass
        with current_rag_budget().model_attempt(prompt_bytes=10, output_tokens=10):
            pass
        with pytest.raises(RagBudgetExceeded), current_rag_budget().model_attempt(prompt_bytes=1, output_tokens=1):
            pass
        with budget.tool_attempt("search"):
            pass
        with budget.tool_attempt("read"):
            pass
        for action in ("search", "read"):
            with pytest.raises(RagBudgetExceeded), budget.tool_attempt(action):
                pass
        assert executor.submit(current_rag_budget).result() is None
    assert current_rag_budget() is None
    assert budget.searches == 2 and budget.reads == 1


def test_token_and_time_limits_prevent_attempt_dispatch():
    for budget in (RagBudget(max_tokens=10), RagBudget(max_seconds=0)):
        with pytest.raises(RagBudgetExceeded), budget.model_attempt(prompt_bytes=10, output_tokens=1):
            pass
        assert budget.calls == 0


def test_feedback_search_and_regeneration_each_have_a_single_request_slot():
    budget = RagBudget(max_searches=3)
    budget.note_query('original')
    budget.begin_feedback()
    with budget.tool_attempt('search', query='focused original'):
        pass
    with budget.model_attempt(prompt_bytes=10, output_tokens=10, purpose='regeneration'):
        pass
    with pytest.raises(RagBudgetExceeded):
        budget.begin_feedback()
    with pytest.raises(RagBudgetExceeded), budget.model_attempt(prompt_bytes=10, output_tokens=10, purpose='regeneration'):
        pass
    with pytest.raises(RagBudgetExceeded), budget.tool_attempt('search', query='FOCUSED original'):
        pass
    assert budget.feedback_attempts == budget.regenerations == budget.calls == 1
    assert budget.searches == 2


def test_regeneration_cannot_run_before_feedback_or_reinitialize_budget():
    budget = RagBudget()
    with pytest.raises(RagBudgetExceeded), budget.model_attempt(prompt_bytes=1, output_tokens=1, purpose='regeneration'):
        pass
    assert budget.calls == budget.regenerations == 0
