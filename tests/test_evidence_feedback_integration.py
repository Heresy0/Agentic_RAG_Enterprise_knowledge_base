"""Real scoped SQL/Milvus feedback loop; model outputs are deterministic."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import delete

from test_agentic_rag_integration import scenario, LocalEmbeddings
from test_milvus_integration import postgres_session, vector_store
from backend.app.db.models import KnowledgeDocument, DocumentChunk, KnowledgeBaseUserGrant
from backend.app.evaluation.trace import capture_trace
from backend.app.knowledge.answer_models import GenerationAnswerDraft
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.milvus_store import make_vector_records
from backend.app.model_usage import usage_context
from backend.app.security.authorization import AuthorizationDenied

pytestmark = pytest.mark.integration
QUESTION = "alpha 与 beta workflow分别是什么？"


@pytest.fixture
def feedback_scenario(scenario):
    session, scope, actor, retrieval, chunks = scenario
    doc = KnowledgeDocument(id=uuid4(), tenant_id=scope.tenant_id, knowledge_base_id=scope.knowledge_base_id,
        source_id=uuid4().hex*2, file_name='beta-workflow.txt', storage_uri='internal/beta.txt', mime_type='text/plain',
        content_hash='b'*64, status='ready')
    session.add(doc)
    session.flush()
    chunk = DocumentChunk(id=uuid4().hex*2, tenant_id=scope.tenant_id, knowledge_base_id=scope.knowledge_base_id,
        document_id=doc.id, chunk_index=0, content='beta workflow approved evidence', keyword_text='beta workflow approved evidence',
        content_hash='b'*64, chunking_version='test', embedding_model='fake', vector_generation=uuid4().hex)
    session.add(chunk)
    session.flush()
    retrieval._vector_store.upsert(make_vector_records([chunk], LocalEmbeddings().embed_documents([chunk.content])))
    session.commit()
    first = GenerationAnswerDraft.model_validate(dict(answerable=False, response_type='refuse', claims=[], refusal_reason='缺少beta证据',
        coverage=[dict(requirement_id='Q1', aspect='beta workflow', status='insufficient', claim_indices=[], missing_information='缺少beta证据')],
        evidence_gap=dict(reason='missing_evidence', requirement_ids=['Q1'], source_quote='beta')))
    return session, scope, actor, retrieval, chunks, doc, first


def build(feedback_scenario, monkeypatch, *, phase=None):
    session, scope, actor, retrieval, chunks, beta, first = feedback_scenario
    counters = dict(searches=0, regenerations=0)
    original_search = retrieval.search
    def revoke():
        session.execute(delete(KnowledgeBaseUserGrant).where(KnowledgeBaseUserGrant.user_id == actor))
        session.commit()
    def search(query, **kwargs):
        counters['searches'] += 1
        docs = original_search(query, **kwargs)
        if counters['searches'] == 1:
            return [doc for doc in docs if doc.metadata['document_id'] != str(beta.id)]
        if phase == 'search':
            revoke()
        # Keep real rehydrated data, prioritize the matched beta result for this
        # deterministic ranking fixture. No foreign result can be manufactured.
        docs.sort(key=lambda doc: doc.metadata['document_id'] != str(beta.id))
        assert any(doc.metadata['document_id'] == str(beta.id) for doc in docs)
        return docs
    retrieval.search = search
    def first_invoke(messages):
        if phase == 'first_generation':
            revoke()
        return first
    first_factory = lambda **kwargs: SimpleNamespace(with_structured_output=lambda *args, **kwargs:
        SimpleNamespace(invoke=first_invoke))
    monkeypatch.setattr('backend.app.knowledge.answer_service.ChatOpenAI', first_factory)
    def regenerate(messages):
        counters['regenerations'] += 1
        if phase == 'regeneration':
            revoke()
        if phase == 'document_change':
            # SQL current-version checks must reject this evidence snapshot.
            session.delete(beta)
            session.commit()
        # Alpha first, beta second after the existing interleaved merge.
        result = GenerationAnswerDraft.model_validate(dict(answerable=True, response_type='answer', refusal_reason=None, evidence_gap=None,
            claims=[dict(text='alpha workflow approved evidence', citations=['资料1']),
                    dict(text='beta workflow approved evidence', citations=['资料2'])],
            coverage=[dict(requirement_id='Q1', aspect='双方workflow', status='answered', claim_indices=[1, 2], missing_information=None)]))
        return dict(raw=AIMessage(content='', usage_metadata=dict(input_tokens=100, output_tokens=20, total_tokens=120)),
                    parsed=result, parsing_error=None)
    factory = lambda **kwargs: SimpleNamespace(with_structured_output=lambda *args, **kwargs:
        SimpleNamespace(invoke=regenerate))
    settings = SimpleNamespace(rag_feedback_enabled=True, agentic_rag_mode='off', rag_planner_mode='off',
        rag_repair_enabled=False, chat_model='fake', chat_base_url='https://fake.invalid', chat_api_key='fake')
    service = AnswerService(settings=settings, retrieval_service=retrieval, regeneration_model_factory=factory)
    return service, counters


def execute(feedback_scenario, service):
    session, scope, actor, *_ = feedback_scenario
    with usage_context(scope=scope, session=session, actor_user_id=actor, actor_external_subject=str(actor)), capture_trace() as trace:
        result = service.answer(QUESTION, scope=scope, session=session)
    return result, trace


def test_feedback_uses_real_sql_milvus_and_never_other_knowledge_base(feedback_scenario, monkeypatch):
    service, counters = build(feedback_scenario, monkeypatch)
    result, trace = execute(feedback_scenario, service)
    assert result.answerable and counters == dict(searches=2, regenerations=1)
    assert 'private secret' not in result.answer and 'private secret' not in str(trace.to_dict())
    assert all(str(citation.knowledge_base_id) == str(feedback_scenario[1].knowledge_base_id) for citation in result.citations)
    events = trace.stages['model_usage']
    assert [row['stage'] for row in events] == ['answer_generation', 'answer_regeneration']


@pytest.mark.parametrize('phase', ['first_generation', 'search', 'regeneration'])
def test_real_authorization_revocation_stops_feedback_at_each_boundary(feedback_scenario, monkeypatch, phase):
    service, counters = build(feedback_scenario, monkeypatch, phase=phase)
    with pytest.raises(AuthorizationDenied):
        execute(feedback_scenario, service)
    assert counters['regenerations'] == (1 if phase == 'regeneration' else 0)


def test_deleted_evidence_during_regeneration_keeps_refusal(feedback_scenario, monkeypatch):
    service, counters = build(feedback_scenario, monkeypatch, phase='document_change')
    result, trace = execute(feedback_scenario, service)
    assert not result.answerable and not result.citations
    assert counters == dict(searches=2, regenerations=1)
    assert any(row.get('outcome') == 'evidence_changed_after_generation' for row in trace.stages['rag_routing'])


def test_refusal_coverage_normalization_does_not_relax_real_final_checks(feedback_scenario, monkeypatch):
    from backend.app.knowledge.answer_models import AnswerCoverage
    first = feedback_scenario[-1]
    first.coverage.insert(0, AnswerCoverage(requirement_id='Q1', aspect='alpha workflow', status='answered',
                                           claim_indices=[], missing_information=None))
    service, counters = build(feedback_scenario, monkeypatch)
    result, trace = execute(feedback_scenario, service)
    assert result.answerable and counters == dict(searches=2, regenerations=1)
    assert first.coverage[0].status == 'answered'
    assert trace.stages['answer_generation'][0]['coverage']['errors']


@pytest.mark.parametrize('unsupported', [False, True])
def test_partial_refusal_claims_require_real_evidence_before_feedback(feedback_scenario, monkeypatch, unsupported):
    from backend.app.knowledge.answer_models import AnswerCoverage, AnswerClaim
    first = feedback_scenario[-1]
    first.claims = [AnswerClaim(text='alpha workflow approved evidence' if not unsupported else 'alpha耗时95分钟',
                               citations=['资料1'])]
    first.coverage.insert(0, AnswerCoverage(requirement_id='Q1', aspect='alpha workflow', status='answered',
                                           claim_indices=[1], missing_information=None))
    service, counters = build(feedback_scenario, monkeypatch)
    result, trace = execute(feedback_scenario, service)
    assert result.answerable is (not unsupported)
    assert counters['searches'] == (1 if unsupported else 2)
    assert counters['regenerations'] == (0 if unsupported else 1)
    assert len(first.claims) == 1
