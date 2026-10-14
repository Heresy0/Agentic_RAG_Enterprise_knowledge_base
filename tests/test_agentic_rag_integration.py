"""Actual PostgreSQL/Milvus tools; embeddings, ranking and decisions are local."""
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.embeddings import Embeddings
import pytest

from test_milvus_integration import postgres_session, vector_store
from backend.app.db.models import Tenant, User, KnowledgeBase, KnowledgeBaseUserGrant, KnowledgeDocument, DocumentChunk
from backend.app.evaluation.trace import capture_trace
from backend.app.knowledge.evidence_coordinator import EvidenceCoordinator
from backend.app.knowledge.milvus_store import make_vector_records
from backend.app.knowledge.retrieval_service import RetrievalService
from backend.app.knowledge.reranker import BaseReranker, RerankResult
from backend.app.model_usage import usage_context
from backend.app.security.authorization import AuthorizationDenied
from backend.app.security.retrieval_scope import RetrievalScope

pytestmark = pytest.mark.integration


class LocalEmbeddings(Embeddings):
    def embed_query(self, text):
        return [1.0]+[0.0]*1023
    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]


class LocalRanking(BaseReranker):
    def rerank(self, *, query, documents, top_n):
        return [RerankResult(index, 1.0) for index in range(min(top_n,len(documents)))]


@pytest.fixture
def scenario(postgres_session, vector_store):
    session = postgres_session
    tenant, actor, allowed, forbidden = [uuid4() for _ in range(4)]
    session.add(Tenant(id=tenant, name='Agent 专用测试租户'))
    session.flush()
    session.add_all([User(id=actor, tenant_id=tenant, external_subject=str(actor), name='测试用户'),
        KnowledgeBase(id=allowed, tenant_id=tenant, name='允许', visibility='restricted'),
        KnowledgeBase(id=forbidden, tenant_id=tenant, name='禁止', visibility='restricted')])
    session.flush()
    session.add(KnowledgeBaseUserGrant(tenant_id=tenant, knowledge_base_id=allowed, user_id=actor, permission='viewer'))
    chunks=[]
    for kb, text in [(allowed,'alpha workflow approved evidence'), (forbidden,'private secret evidence')]:
        doc=KnowledgeDocument(id=uuid4(), tenant_id=tenant, knowledge_base_id=kb, source_id=uuid4().hex*2,
            file_name=str(kb)+'.txt', storage_uri='private/internal', mime_type='text/plain', content_hash='a'*64,
            status='ready')
        session.add(doc)
        session.flush()
        group=[]
        for index in range(8):
            chunk=DocumentChunk(id=uuid4().hex*2, tenant_id=tenant, knowledge_base_id=kb, document_id=doc.id,
                chunk_index=index, content=text+str(index), keyword_text=text, content_hash='a'*64,
                chunking_version='test', embedding_model='fake', vector_generation=uuid4().hex if index==0 else group[0].vector_generation)
            session.add(chunk)
            group.append(chunk)
        session.flush()
        vector_store.upsert(make_vector_records(group, LocalEmbeddings().embed_documents([text]*8)))
        chunks.extend(group)
    session.commit()
    retrieval=RetrievalService(SimpleNamespace(),vector_store=vector_store,embeddings=LocalEmbeddings(),reranker=LocalRanking())
    return session, RetrievalScope(tenant,allowed), actor, retrieval, chunks


def test_agent_search_read_loop_uses_real_scoped_stores(scenario):
    session, scope, actor, retrieval, chunks = scenario
    decisions=iter([dict(action='search',reason='cross_document',query='alpha approved limits'),
                    dict(action='read',reason='missing_evidence',evidence_id='E1')])
    coordinator=EvidenceCoordinator(SimpleNamespace(agentic_rag_mode='always'),retrieval,
        decider=SimpleNamespace(decide=lambda *args,**kwargs:next(decisions)))
    with usage_context(scope=scope, session=session, actor_user_id=actor, actor_external_subject=str(actor)), capture_trace() as trace:
        result=coordinator.collect('Compare alpha workflows',scope=scope,session=session)
    assert result.documents
    assert all(doc.metadata['knowledge_base_id']==str(scope.knowledge_base_id) for doc in result.documents)
    assert all('private secret' not in doc.page_content for doc in result.documents)
    summary=trace.stages['agent_decisions'][-1]
    assert summary['searches']==2 and summary['reads']==1
    assert not any(row.get('scope_violation') for row in trace.stages['agent_evidence'])


def test_revocation_during_agent_model_step_stops_real_store_tools(scenario):
    session, scope, actor, retrieval, _ = scenario
    def decide(*args,**kwargs):
        from sqlalchemy import delete
        session.execute(delete(KnowledgeBaseUserGrant).where(KnowledgeBaseUserGrant.user_id==actor))
        session.commit()
        return dict(action='read',reason='missing_evidence',evidence_id='E1')
    coordinator=EvidenceCoordinator(SimpleNamespace(agentic_rag_mode='always'),retrieval,decider=SimpleNamespace(decide=decide))
    with usage_context(scope=scope, session=session, actor_user_id=actor, actor_external_subject=str(actor)), pytest.raises(AuthorizationDenied):
        coordinator.collect('Compare workflows',scope=scope,session=session)


def test_input_planner_and_agent_use_same_real_authorization(scenario):
    from backend.app.knowledge.query_planner import QueryPlanner
    from backend.app.knowledge.rag_budget import RagBudget, rag_budget_scope
    session, scope, actor, retrieval, _ = scenario
    settings = SimpleNamespace(rag_planner_mode='auto', agentic_rag_mode='auto', chat_model='fake',
                               chat_base_url='https://fake.invalid', chat_api_key='fake')
    result = dict(intent='comparison', strategy='agentic', reason='multiple_aspects', subquestions=[
        dict(question='alpha workflow', source_quote='alpha', depends_on=[]),
        dict(question='beta workflow', source_quote='beta', depends_on=[])])
    factory = lambda **kwargs: SimpleNamespace(with_structured_output=lambda *args, **kwargs:
        SimpleNamespace(invoke=lambda messages: result))
    planner = QueryPlanner(settings, model_factory=factory)
    coordinator = EvidenceCoordinator(settings, retrieval, decider=SimpleNamespace(
        decide=lambda *args, **kwargs: dict(action='finish', reason='cannot_help')))
    with usage_context(scope=scope, actor_user_id=actor, actor_external_subject=str(actor)), \
            rag_budget_scope(RagBudget()) as budget, capture_trace() as trace:
        plan = planner.plan('比较 alpha 与 beta workflow', scope=scope, session=session)
        collected = coordinator.collect('比较 alpha 与 beta workflow', scope=scope, session=session, plan=plan)
    assert plan.strategy == 'agentic' and budget.calls == 2
    assert all(doc.metadata['knowledge_base_id'] == str(scope.knowledge_base_id) for doc in collected.documents)
    assert 'private secret' not in str(trace.to_dict())


@pytest.mark.parametrize('phase', ['planner', 'repair'])
def test_new_model_phases_cannot_continue_after_real_grant_revocation(scenario, phase):
    from sqlalchemy import delete
    from backend.app.knowledge.query_planner import QueryPlanner
    from backend.app.knowledge.answer_repair import AnswerRepair
    from backend.app.knowledge.answer_models import GenerationAnswerDraft
    from backend.app.knowledge.answer_validation import AnswerValidator
    from backend.app.knowledge.context_builder import ContextBuilder
    from backend.app.knowledge.rag_budget import RagBudget, rag_budget_scope
    session, scope, actor, retrieval, _ = scenario
    settings = SimpleNamespace(rag_planner_mode='auto', rag_repair_enabled=True, chat_model='fake',
                               chat_base_url='https://fake.invalid', chat_api_key='fake')
    question = '比较 alpha 与 beta workflow' if phase == 'planner' else 'alpha workflow是什么？'
    context = ContextBuilder().build(retrieval.search(question, scope=scope, session=session), question=question)
    draft = GenerationAnswerDraft.model_validate(dict(answerable=True, claims=[
        dict(text='alpha workflow approved evidence', citations=['资料1'])], coverage=[
        dict(requirement_id='Q1', aspect='workflow', status='answered', claim_indices=[2], missing_information=None)],
        refusal_reason=None))
    fixed = draft.model_copy(deep=True)
    fixed.coverage[0].claim_indices = [1]
    def invoke(messages):
        session.execute(delete(KnowledgeBaseUserGrant).where(KnowledgeBaseUserGrant.user_id == actor))
        session.commit()
        return dict(intent='comparison', strategy='agentic', reason='multiple_aspects', subquestions=[]) if phase == 'planner' else fixed
    factory = lambda **kwargs: SimpleNamespace(with_structured_output=lambda *args, **kwargs: SimpleNamespace(invoke=invoke))
    with usage_context(scope=scope, actor_user_id=actor, actor_external_subject=str(actor)), \
            rag_budget_scope(RagBudget()), pytest.raises(AuthorizationDenied):
        if phase == 'planner':
            QueryPlanner(settings, model_factory=factory).plan(question, scope=scope, session=session)
        else:
            AnswerRepair(settings, model_factory=factory).repair(draft, question=question, context=context,
                validator=AnswerValidator(), scope=scope, session=session, refresh_context=lambda: True)
