"""Opt-in real PostgreSQL transaction/JSON-CAS checks, with an isolated synthetic tenant."""
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.db.models import KnowledgeBase, Tenant, User
from backend.app.knowledge.answer_models import AnswerResult
from backend.app.knowledge.conversation_memory import MemoryResolution, Proposal
from backend.app.knowledge.conversation_service import ConversationAnswerService
from backend.app.knowledge.conversations import ConversationBusy, ConversationStore
from backend.app.security.principal import Principal
from backend.app.security.retrieval_scope import RetrievalScope

pytestmark = pytest.mark.skipif(os.getenv('RUN_POSTGRES_MEMORY_TESTS') != '1',
                              reason='Explicit local PostgreSQL integration opt-in required')


def test_postgres_memory_turn_atomicity_and_privacy_cutoff_cas():
    engine = create_engine(Settings().database_url)
    assert engine.dialect.name == 'postgresql'
    tenant_id = None
    try:
        with Session(engine, expire_on_commit=False) as session:
            tenant = Tenant(name=f'memory-integration-{uuid4().hex}')
            session.add(tenant); session.flush(); tenant_id = tenant.id
            subject = uuid4().hex
            user = User(tenant_id=tenant_id, name='Synthetic memory test', external_subject=subject)
            kb = KnowledgeBase(tenant_id=tenant_id, name='Synthetic KB', visibility='company')
            session.add_all([user, kb]); session.commit()
            principal, scope = Principal(user.id, tenant_id, subject), RetrievalScope(tenant_id, kb.id)
            store = ConversationStore(session, principal, scope)
            cid = store.create().id
            response = AnswerResult(answer='synthetic refusal', answerable=False, citations=[])
            class Answer:
                def answer(self, *args, **kwargs):
                    return response
            class Resolver:
                _settings = SimpleNamespace(conversation_memory_enabled=True)
                def resolve(self, q, turns):
                    return q
                def resolve_memory(self, q, turns, state, index):
                    return MemoryResolution(question='青屿2024年要求？', updates=[] if state.items else [Proposal(
                        object='青屿', field='time', value='2024年', turn=1, quote='青屿2024年标准')])
            service = ConversationAnswerService(Answer(), Resolver())
            for index in range(7):
                service.answer('青屿2024年标准' if index == 0 else '它的要求？', conversation_id=cid,
                               principal=principal, scope=scope, session=session)
            with Session(engine) as restored:
                state = ConversationStore(restored, principal, scope).context_memory(cid)
                assert state.items[0].value == '2024年' and state.covered_through == 7
            before = store.get(cid).memory_json
            def fail_audit(_):
                raise RuntimeError('synthetic audit failure')
            with pytest.raises(RuntimeError, match='synthetic audit'):
                service.answer('它的要求？', conversation_id=cid, principal=principal, scope=scope,
                               session=session, commit_success=fail_audit)
            assert store.get(cid).turn_count == 7 and store.get(cid).memory_json == before
            assert store.get(cid).lease_token is None
            token = store.acquire(cid)
            state = store.context_memory(cid, token=token)
            store._checkpoint(store.get(cid), token=token)
            with pytest.raises(ConversationBusy):
                store.append(cid, token, 'q', 'q', response, memory=state, expected_cutoff=state.cutoff)
            store.release(cid, token)
            assert store.get(cid).turn_count == 7 and store.get(cid).memory_json['cutoff'] == 7
    finally:
        if tenant_id is not None:
            with Session(engine) as cleanup:
                cleanup.execute(delete(Tenant).where(Tenant.id == tenant_id)); cleanup.commit()
        engine.dispose()
