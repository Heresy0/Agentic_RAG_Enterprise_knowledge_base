"""Schema recovery uses real scoped SQL/Milvus and rechecks authorization."""
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

from test_agentic_rag_integration import scenario
from test_milvus_integration import postgres_session, vector_store
from backend.app.db.models import KnowledgeBaseUserGrant, KnowledgeDocument
from backend.app.evaluation.trace import capture_trace
from backend.app.knowledge.answer_models import GenerationAnswerDraft
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.answer_repair import AnswerRepair
from backend.app.model_usage import usage_context
from backend.app.security.authorization import AuthorizationDenied

pytestmark=pytest.mark.integration


@pytest.mark.parametrize('phase',[None,'revoke','delete'])
def test_schema_recovery_real_permission_and_document_boundaries(scenario,monkeypatch,phase):
    session,scope,actor,retrieval,chunks=scenario
    counters={'calls':0}
    def invalid(messages):
        return GenerationAnswerDraft.model_validate({'answerable':True,'response_type':'answer','coverage':[]})
    monkeypatch.setattr('backend.app.knowledge.answer_service.ChatOpenAI',lambda **kwargs:
        SimpleNamespace(with_structured_output=lambda *args,**kwargs:SimpleNamespace(invoke=invalid)))
    def recover(messages):
        counters['calls']+=1
        if phase=='revoke':
            session.execute(delete(KnowledgeBaseUserGrant).where(KnowledgeBaseUserGrant.user_id==actor))
            session.commit()
        if phase=='delete':
            session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id==chunks[0].document_id))
            session.commit()
        return GenerationAnswerDraft.model_validate(dict(answerable=True,response_type='answer',claims=[dict(
            text='alpha workflow approved evidence',citations=['资料1'])],coverage=[dict(
            requirement_id='Q1',aspect='workflow',status='answered',claim_indices=[1],missing_information=None)]))
    settings=SimpleNamespace(rag_repair_enabled=True,rag_planner_mode='off',agentic_rag_mode='off',
        chat_model='fake',chat_base_url='https://fake.invalid',chat_api_key='fake')
    service=AnswerService(settings=settings,retrieval_service=retrieval)
    service._answer_repair=AnswerRepair(settings,model_factory=lambda **kwargs:
        SimpleNamespace(with_structured_output=lambda *args,**kwargs:SimpleNamespace(invoke=recover)))
    with usage_context(scope=scope,session=session,actor_user_id=actor,actor_external_subject=str(actor)),capture_trace() as trace:
        if phase=='revoke':
            with pytest.raises(AuthorizationDenied):
                service.answer('alpha workflow?',scope=scope,session=session)
        else:
            result=service.answer('alpha workflow?',scope=scope,session=session)
            assert result.answerable is (phase is None)
            assert all(str(c.knowledge_base_id)==str(scope.knowledge_base_id) for c in result.citations)
        assert counters['calls']==1
        assert 'private secret' not in str(trace.to_dict())
        assert next(e for e in trace.stages['rag_routing'] if e['kind']=='budget')['calls']==1
