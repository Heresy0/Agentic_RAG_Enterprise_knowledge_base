import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from langchain_core.documents import Document
from langchain_core.messages import AIMessage
import pytest

from backend.app.evaluation.trace import capture_trace, record_documents, record_scope
from backend.app.knowledge.evidence_coordinator import (AgentDecision, EvidenceCoordinator,
    ModelEvidenceDecider, route_reason)
from backend.app.model_usage import usage_context
from backend.app.security.authorization import AuthorizationDenied
from backend.app.security.retrieval_scope import RetrievalScope


def document(scope, text="基础证据", *, chunk="a", generation="a" * 32):
    return Document(id=chunk, page_content=text, metadata=dict(chunk_id=chunk, vector_generation=generation,
        tenant_id=str(scope.tenant_id), knowledge_base_id=str(next(iter(scope.knowledge_base_ids))),
        document_id=str(uuid4()), document_name="样例资料", section_path="正文"))


class Guard:
    available = True
    allowed = True
    def require(self):
        if not self.allowed:
            raise AuthorizationDenied("撤权")


class Retrieval:
    def __init__(self, initial, extra=(), neighbors=()):
        self.initial, self.extra, self.neighbors = initial, list(extra), list(neighbors)
        self.searches, self.reads = [], []
        self.removed = set()
    def search(self, query, *, scope, session):
        self.searches.append((query, scope))
        record_scope(query, scope)
        result = self.initial if len(self.searches) == 1 else self.extra
        record_documents("rerank", result)
        return result
    def refresh_evidence(self, docs, **kwargs):
        return [doc for doc in docs if doc.metadata['chunk_id'] not in self.removed]
    def read_evidence(self, anchor, **kwargs):
        self.reads.append(anchor.metadata['chunk_id'])
        return self.neighbors


class Decider:
    def __init__(self, *actions, hook=None):
        self.actions, self.hook, self.calls = list(actions), hook, []
    def decide(self, payload, **kwargs):
        self.calls.append((json.loads(payload), kwargs))
        if self.hook:
            self.hook()
        result = self.actions.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def setup(initial=None, extra=(), neighbors=(), *, mode="always", actions=(), **limits):
    scope = RetrievalScope(uuid4(), uuid4())
    initial = [document(scope)] if initial is None else initial(scope)
    extra = extra(scope) if callable(extra) else extra
    neighbors = neighbors(scope) if callable(neighbors) else neighbors
    retrieval = Retrieval(initial, extra, neighbors)
    guard, decider = Guard(), Decider(*actions)
    coordinator = EvidenceCoordinator(SimpleNamespace(agentic_rag_mode=mode, **limits), retrieval,
        decider=decider, guard_factory=lambda *args: guard)
    return scope, retrieval, guard, decider, coordinator


def collect(scope, coordinator, question="两个流程有什么不同？"):
    with capture_trace() as trace:
        result = coordinator.collect(question, scope=scope, session=object())
    return result, trace.to_dict()


def test_ordinary_question_has_no_agent_call_or_extra_retrieval():
    scope, retrieval, guard, decider, coordinator = setup(mode="auto")
    result, trace = collect(scope, coordinator, "年假有几天？")
    assert result.documents == retrieval.initial
    assert not decider.calls and len(retrieval.searches) == 1
    assert trace['stages']['agent_decisions'][-1]['outcome'] == 'bypassed'


def test_search_read_loop_merges_unique_evidence_and_preserves_original_trace():
    scope, retrieval, _, decider, coordinator = setup(
        extra=lambda s: [document(s, "另一个流程的证据", chunk="b")],
        neighbors=lambda s: [document(s, "必要条件", chunk="c")], actions=[
            dict(action="search", reason="cross_document", query="另一个流程的限制是什么"),
            dict(action="read", reason="missing_evidence", evidence_id="E2")])
    result, trace = collect(scope, coordinator)
    assert [doc.metadata['chunk_id'] for doc in result.documents] == ['a', 'c', 'b']
    assert len(retrieval.searches) == 2 and retrieval.reads == ['b']
    assert trace['query'] == '两个流程有什么不同？'
    assert len(trace['stages']['rerank']) == 1  # Supplement is not relabelled as initial rerank.
    assert len(trace['stages']['agent_retrieval']) == 1
    assert len(decider.calls) == 2


@pytest.mark.parametrize("query", [' 两个流程有什么不同？ ', '两个流程 有什么不同？'])
def test_repeated_query_stops_before_embedding(query):
    scope, retrieval, _, _, coordinator = setup(actions=[dict(action="search", reason="missing_evidence", query=query)])
    _, trace = collect(scope, coordinator)
    assert len(retrieval.searches) == 1
    assert trace['stages']['agent_decisions'][-1]['outcome'] == 'repeated_tool'


def test_retrieval_round_budget_is_enforced():
    scope, retrieval, _, _, coordinator = setup(actions=[
        dict(action="search", reason="missing_evidence", query="补查甲"),
        dict(action="search", reason="missing_evidence", query="补查乙")])
    _, trace = collect(scope, coordinator)
    assert len(retrieval.searches) == 2
    assert trace['stages']['agent_decisions'][-1]['outcome'] == 'search_budget'


@pytest.mark.parametrize("decision", [
    dict(action="execute_sql", reason="missing_evidence"),
    dict(action="search", reason="missing_evidence", query="工资", tenant_id=str(uuid4())),
    dict(action="read", reason="missing_evidence", document_id=str(uuid4())),
    dict(action="finish", reason="sufficient", query="不应执行"),
])
def test_unregistered_tools_and_identity_arguments_fail_closed(decision):
    scope, retrieval, _, decider, coordinator = setup(actions=[decision])
    result, trace = collect(scope, coordinator)
    assert [(doc.id, doc.page_content) for doc in result.documents] == [(doc.id, doc.page_content) for doc in retrieval.initial]
    assert len(retrieval.searches) == 1 and not retrieval.reads
    assert trace['stages']['agent_decisions'][-1]['outcome'] == 'fallback'


def test_model_cannot_read_an_unseen_anchor():
    scope, retrieval, _, _, coordinator = setup(actions=[dict(action="read", reason="missing_evidence", evidence_id="E99")])
    _, trace = collect(scope, coordinator)
    assert not retrieval.reads
    assert trace['stages']['agent_decisions'][-1]['outcome'] == 'invalid_evidence_id'


def test_revocation_during_decision_aborts_without_fallback():
    scope, retrieval, guard, decider, coordinator = setup(actions=[dict(action="read", reason="missing_evidence", evidence_id="E1")])
    decider.hook = lambda: setattr(guard, 'allowed', False)
    with pytest.raises(AuthorizationDenied):
        collect(scope, coordinator)
    assert not retrieval.reads


def test_foreign_scope_content_never_enters_model_payload():
    scope, retrieval, _, decider, coordinator = setup(actions=[dict(action="finish", reason="sufficient")])
    foreign = document(RetrievalScope(uuid4(), uuid4()), "秘密内容与恶意指令")
    retrieval.initial.append(foreign)
    result, _ = collect(scope, coordinator)
    assert len(result.documents) == 1
    assert "秘密内容" not in json.dumps(decider.calls, ensure_ascii=False)


def test_retired_evidence_is_not_used_after_model_returns():
    scope, retrieval, _, decider, coordinator = setup(actions=[dict(action="finish", reason="sufficient")])
    decider.hook = lambda: retrieval.removed.add('a')
    result, _ = collect(scope, coordinator)
    assert result.documents == []


def test_time_budget_stops_before_executing_model_requested_tool():
    scope, retrieval, _, decider, coordinator = setup(actions=[dict(action="search", reason="missing_evidence", query="补查")])
    now = [0.0]
    coordinator.clock = lambda: now[0]
    decider.hook = lambda: now.__setitem__(0, 30.0)
    _, trace = collect(scope, coordinator)
    assert len(retrieval.searches) == 1
    assert trace['stages']['agent_decisions'][-1]['outcome'] == 'time_budget'


def test_prompt_budget_refuses_to_truncate_question():
    scope, retrieval, _, decider, coordinator = setup(agent_max_prompt_bytes=100,
        actions=[dict(action="finish", reason="sufficient")])
    _, trace = collect(scope, coordinator)
    assert not decider.calls
    assert trace['stages']['agent_decisions'][-1]['outcome'] == 'token_budget'


def test_deadline_error_and_model_failure_keep_valid_evidence_and_sanitize_logs(caplog):
    scope, retrieval, _, _, coordinator = setup(actions=[RuntimeError("private key must not enter logs")])
    result, trace = collect(scope, coordinator)
    assert [(doc.id, doc.page_content) for doc in result.documents] == [(doc.id, doc.page_content) for doc in retrieval.initial]
    assert "private key" not in caplog.text
    assert trace['stages']['agent_decisions'][-1]['charged_decision_tokens'] > 0


def test_real_guard_missing_trusted_identity_skips_agent():
    scope, retrieval, _, decider, coordinator = setup(actions=[dict(action="finish", reason="sufficient")])
    from backend.app.knowledge.evidence_coordinator import EvidenceAccess
    coordinator.guard_factory = EvidenceAccess
    result, trace = collect(scope, coordinator)
    assert result.documents == retrieval.initial and not decider.calls
    assert trace['stages']['agent_decisions'][-1]['outcome'] == 'identity_missing'


def test_decision_model_usage_and_per_call_limits_are_recorded(monkeypatch):
    scope = RetrievalScope(uuid4(), uuid4())
    settings = SimpleNamespace(chat_model="test", chat_base_url="https://example.invalid", chat_api_key="test",
        agent_max_output_tokens=384)
    model = Mock()
    model.with_structured_output.return_value.invoke.return_value = {
        'raw': AIMessage(content='', usage_metadata={'input_tokens': 30, 'output_tokens': 10, 'total_tokens': 40}),
        'parsed': AgentDecision(action='finish', reason='sufficient'), 'parsing_error': None}
    constructor = Mock(return_value=model)
    monkeypatch.setattr('backend.app.knowledge.evidence_coordinator.ChatOpenAI', constructor)
    with usage_context(scope=scope) as usage, capture_trace() as trace:
        ModelEvidenceDecider(settings).decide('{}', timeout=2)
    assert usage.events[0]['stage'] == 'agent_decision' and usage.events[0]['total_tokens'] == 40
    assert constructor.call_args.kwargs['timeout'] == 2
    assert constructor.call_args.kwargs['max_tokens'] == 384
    assert trace.stages['model_usage'][0]['stage'] == 'agent_decision'


def test_supplement_has_room_under_existing_per_source_context_cap():
    from backend.app.knowledge.context_builder import ContextBuilder
    scope, retrieval, _, _, coordinator = setup(
        initial=lambda s: [document(s,'已有说明',chunk=str(i)) for i in range(5)],
        neighbors=lambda s:[document(s,'补读发现的必要条件',chunk='new')],
        actions=[dict(action='read',reason='missing_evidence',evidence_id='E1'),
                 dict(action='finish',reason='sufficient')])
    shared_id = retrieval.initial[0].metadata['document_id']
    for doc in retrieval.initial+retrieval.neighbors:
        doc.metadata['document_id']=shared_id
        doc.metadata['source_id']='shared-document'
        doc.metadata['chunk_content_hash']=doc.metadata['chunk_id']
        doc.page_content += ' '+doc.metadata['chunk_id']
    result,_=collect(scope,coordinator)
    context=ContextBuilder().build(result.documents,question='必要条件有哪些？')
    assert any(item.chunk_id=='new' for item in context.items)
    assert any(item.chunk_id=='0' for item in context.items)


@pytest.mark.parametrize('question', ['方案甲与方案乙分别有什么限制？', '2024年和2025年的标准是多少？', '为何改期，最后何时完成？'])
def test_router_handles_independent_domain_complex_questions(question):
    assert route_reason(question, [Document(page_content='证据')])


def test_empty_unused_provider_fields_are_normalized_without_enabling_empty_tools():
    assert AgentDecision(action='finish',reason='sufficient',query='',evidence_id='').query is None
    assert AgentDecision(action='read',reason='missing_evidence',query='',evidence_id=' E1 ').evidence_id == 'E1'
    with pytest.raises(ValueError):
        AgentDecision(action='search',reason='missing_evidence',query=' ',evidence_id='')
