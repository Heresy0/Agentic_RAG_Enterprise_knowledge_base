"""Manufacturing histories cannot escape through ordinary conversations or snapshots."""
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from test_document_upload_api import api_context, _set_principal
from test_manufacturing_m3_api import harness, ask, create
from backend.app.db.business_models import BusinessScopeGrant, ConversationBusinessBinding
from backend.app.db.conversation_models import ConversationTurn
from backend.app.db.models import KnowledgeBaseUserGrant
from backend.app.db.quality_models import QARun
from backend.app.manufacturing.conversations import ManufacturingConversationStore
from backend.app.security.authorization import AuthorizationService


def path(h, identifier=""):
    return "/api/manufacturing/conversations"+("/"+identifier if identifier else "")+"?knowledge_base_id="+str(h.c.knowledge_base_id)


def test_bound_followups_persist_pair_and_restore_frozen_source_without_contacting_mes(harness):
    h = harness
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
        first = ask(client, h, conversation_id=identifier); assert first.status_code == 200, first.text
        second = ask(client, h, conversation_id=identifier); assert second.status_code == 200, second.text
        before = len(h.source.reads)
        h.source.records["WO-018"]["source_revision"] = "r2"
        detail = client.get(path(h, identifier)); assert detail.status_code == 200, detail.text
        data = detail.json(); assert len(data["turns"]) == 2 and data["turns"][0]["run_id"]
        assert data["turns"][0]["result"]["work_order_context"]["source_revision"] == "r1"
        assert len(h.source.reads) == before
        changed = ask(client, h, conversation_id=identifier)
        assert changed.status_code == 409
        again = ask(client, h, conversation_id=identifier)
        assert again.status_code == 409
        assert len(h.generation.calls) == 2


def test_generic_routes_never_list_read_or_answer_a_manufacturing_conversation(harness):
    h = harness
    with TestClient(h.c.app) as client:
        identifier = create(client, h); assert ask(client, h, conversation_id=identifier).status_code == 200
        normal = f"/api/knowledge-bases/{h.c.knowledge_base_id}/conversations"
        assert client.get(normal).json()["items"] == []
        assert client.get(normal+"/"+identifier).status_code == 404
        before = len(h.generation.calls)
        response = client.post("/api/qa", json=dict(question="继续", knowledge_base_id=str(h.c.knowledge_base_id), conversation_id=identifier))
        assert response.status_code == 404
        assert len(h.generation.calls) == before
        ordinary = client.post(normal).json()["id"]
        assert ask(client, h, conversation_id=ordinary).status_code == 404


def test_work_order_and_other_owner_cannot_rebind_an_existing_conversation(harness):
    h = harness
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
        response = ask(client, h, wo="WO-017", conversation_id=identifier)
        assert response.status_code == 409 and not h.generation.calls
        _set_principal(h.c, h.c.viewer)
        with h.c.session_factory() as session:
            session.add(BusinessScopeGrant(tenant_id=h.c.tenant_id, user_id=h.c.viewer.user_id,
                source_system_id="mock-mes-hc", plant_code="HC-P01", permission="view")); session.commit()
        assert client.get(path(h, identifier)).status_code == 404
        assert ask(client, h, conversation_id=identifier).status_code == 404


def test_revoked_business_grant_hides_title_history_and_snapshot_and_regrant_cannot_resume(harness):
    h = harness
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
        answer = ask(client, h, conversation_id=identifier).json(); run_id = answer["run_id"]
        with h.c.session_factory() as session:
            session.execute(delete(BusinessScopeGrant)); session.commit()
        assert client.get(path(h)).json()["items"] == []
        assert client.get(path(h, identifier)).status_code == 404
        assert client.get("/api/qa/runs/"+run_id).status_code == 404
        with h.c.session_factory() as session:
            binding = session.scalar(select(ConversationBusinessBinding)); assert binding.invalidated_at is not None
            session.add(BusinessScopeGrant(tenant_id=h.c.tenant_id, user_id=h.c.editor.user_id,
                source_system_id="mock-mes-hc", plant_code="HC-P01", permission="view")); session.commit()
        assert client.get(path(h, identifier)).status_code == 200
        assert ask(client, h, conversation_id=identifier).status_code == 409
        assert len(h.generation.calls) == 1


def test_known_conversation_busy_responds_before_mes_or_generation(harness):
    h = harness
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
        with h.c.session_factory() as session:
            scope = AuthorizationService(session).require_retrieval_scope(principal=h.c.editor, knowledge_base_id=h.c.knowledge_base_id)
            store = ManufacturingConversationStore(session, h.c.editor, scope); token = store.acquire(UUID(identifier))
            before = len(h.source.reads)
            response = ask(client, h, conversation_id=identifier)
            assert response.status_code == 409 and response.json()["detail"]["error_code"] == "conversation_busy"
            assert len(h.source.reads) == before and not h.generation.calls
            store.release(UUID(identifier), token)


def test_disabled_snapshots_keep_private_conversation_without_creating_a_qa_run(harness):
    h = harness; h.settings.qa_snapshots_enabled = False
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
        response = ask(client, h, conversation_id=identifier)
        assert response.status_code == 200, response.text
        assert response.json()["run_id"] is None and response.json()["snapshot_status"] == "not_observed"
    with h.c.session_factory() as session:
        assert len(session.scalars(select(ConversationTurn)).all()) == 1
        assert session.scalars(select(QARun)).all() == []


def test_observed_knowledge_base_revocation_also_invalidates_bound_context(harness):
    h = harness
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
        with h.c.session_factory() as session:
            session.execute(delete(KnowledgeBaseUserGrant).where(KnowledgeBaseUserGrant.user_id == h.c.editor.user_id)); session.commit()
        assert client.get(path(h)).status_code == 403
        with h.c.session_factory() as session:
            assert session.scalar(select(ConversationBusinessBinding)).invalidated_at is not None
            session.add(KnowledgeBaseUserGrant(tenant_id=h.c.tenant_id, knowledge_base_id=h.c.knowledge_base_id,
                user_id=h.c.editor.user_id, permission="editor")); session.commit()
        assert ask(client, h, conversation_id=identifier).status_code == 409


def test_privacy_invalidation_survives_a_memory_checkpoint_cas_failure(harness, monkeypatch):
    from backend.app.knowledge.conversations import ConversationStore, ConversationBusy
    import pytest
    h = harness
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
    with h.c.session_factory() as session:
        scope = AuthorizationService(session).require_retrieval_scope(principal=h.c.editor, knowledge_base_id=h.c.knowledge_base_id)
        store = ManufacturingConversationStore(session, h.c.editor, scope)
        row = store.get(UUID(identifier))
        def conflict(self, row, **kwargs):
            self.session.rollback()
            raise ConversationBusy("simulated CAS conflict")
        monkeypatch.setattr(ConversationStore, "_checkpoint", conflict)
        with pytest.raises(ConversationBusy): store._checkpoint(row)
    with h.c.session_factory() as session:
        assert session.get(ConversationBusinessBinding, (h.c.tenant_id, UUID(identifier))).invalidated_at is not None
