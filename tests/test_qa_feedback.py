"""Exercise the real QA, feedback, authorization and persistence boundary without models."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.documents import Document
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.api import feedback, qa, conversations, quality, verification
from backend.app.db.base import Base
from backend.app.db.dependencies import get_database_session
from backend.app.db.models import AuditEvent, KnowledgeBase, KnowledgeBaseUserGrant, KnowledgeDocument, Tenant, User
from backend.app.db.quality_models import QARun, QARunScope, QAFeedback
from backend.app.evaluation.trace import record_context, record_documents, record_scope
from backend.app.governance.tenant_concurrency_limiter import ConcurrencyDecision, ConcurrencyLease
from backend.app.governance.tenant_rate_limiter import RateLimitDecision
from backend.app.knowledge.answer_models import AnswerResult, Citation
from backend.app.knowledge.context_builder import BuiltContext, ContextItem
from backend.app.knowledge.conversation_service import ConversationAnswerService
from backend.app.quality.capture import capture_snapshot
from backend.app.security.dependencies import get_current_principal
from backend.app.security.principal import Principal
from backend.app.security.retrieval_scope import RetrievalScope


@pytest.fixture
def data():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        tenant, other = Tenant(name="A"), Tenant(name="B")
        session.add_all([tenant, other]); session.flush()
        users = [User(tenant_id=tenant.id, name=name, external_subject=name) for name in ("owner", "peer", "admin", "all_admin")]
        outsider = User(tenant_id=other.id, name="outside", external_subject="outside")
        bases = [KnowledgeBase(tenant_id=tenant.id, name=name, visibility="restricted") for name in ("KB1", "KB2")]
        session.add_all([*users, outsider, *bases]); session.flush()
        for i, user in enumerate(users):
            for j, kb in enumerate(bases):
                if i == 2 and j == 1:
                    continue
                session.add(KnowledgeBaseUserGrant(tenant_id=tenant.id, user_id=user.id,
                    knowledge_base_id=kb.id, permission="admin" if i >= 2 else "viewer"))
        doc = KnowledgeDocument(tenant_id=tenant.id, knowledge_base_id=bases[0].id, source_id="sample",
            file_name="制度.md", storage_uri="file:///private/must-not-leak.md", mime_type="text/markdown",
            content_hash="a" * 64, status="ready", version=3)
        session.add(doc); session.commit()
        principals = [Principal(u.id, u.tenant_id, u.external_subject) for u in users]
        d = SimpleNamespace(session=session, bases=bases, doc=doc, principals=principals,
                            outside=Principal(outsider.id, outsider.tenant_id, "outside"), current=principals[0])

        class Answer:
            def answer(self, question, *, scope, session):
                record_scope(question, scope)
                record_documents("authorized_final", [Document(page_content="旧版例外条件", metadata=dict(
                    tenant_id=str(tenant.id), knowledge_base_id=str(bases[0].id), document_id=str(doc.id),
                    document_version=doc.version, vector_generation="generation-3"))])
                item = ContextItem(citation_id="1", content="旧版例外条件<script>", source=doc.storage_uri,
                    document_id=str(doc.id), document_name=doc.file_name, section_path="适用范围", chunk_id="child",
                    parent_id="parent", rerank_score=0.9, knowledge_base_id=str(bases[0].id))
                record_context(BuiltContext("完整模型上下文", [item], 8))
                return AnswerResult(answer="原始回答<script>", answerable=True, citations=[Citation(
                    citation_id="1", document_id=doc.id, document_name=doc.file_name, section_path="适用范围",
                    knowledge_base_id=bases[0].id, chunk_id="child", parent_id="parent")])

        class Resolver:
            def resolve(self, question, turns):
                return "独立的追问" if turns else question

        app = FastAPI()
        app.include_router(qa.router); app.include_router(feedback.router); app.include_router(conversations.router); app.include_router(quality.router)
        app.include_router(verification.router)
        app.state.settings = SimpleNamespace(qa_snapshots_enabled=True, chat_model="test-model", chat_api_key="secret-api-key",
            qa_rate_limit_requests=60, qa_rate_limit_window_seconds=60,
            qa_max_concurrent_requests_per_tenant=3, qa_concurrency_lease_seconds=180)
        app.state.answer_service = Answer()
        app.state.conversation_answer_service = ConversationAnswerService(app.state.answer_service, Resolver())
        app.state.tenant_rate_limiter = SimpleNamespace(consume=lambda **_: RateLimitDecision(True, 60, 59, 60))
        app.state.tenant_concurrency_limiter = SimpleNamespace(
            acquire=lambda **_: ConcurrencyDecision(True, 3, 1, 2, 0, ConcurrencyLease(tenant.id, "qa", "lease")),
            release=lambda _: True, renew=lambda *_, **__: True)
        app.dependency_overrides[get_database_session] = lambda: session
        app.dependency_overrides[get_current_principal] = lambda: d.current
        with TestClient(app) as client:
            d.client = client
            yield d
    engine.dispose()


def ask(data, *, all_scopes=False, **extra):
    body = {"question": "报销有什么条件？", **extra}
    if not all_scopes:
        body["knowledge_base_id"] = str(data.bases[0].id)
    response = data.client.post("/api/qa", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["snapshot_status"] == "captured"
    return response.json()["run_id"]


def put(data, run_id, **extra):
    return data.client.put(f"/api/qa/runs/{run_id}/feedback", json={"rating": "up", **extra})


def test_answer_snapshot_freezes_actual_evidence_and_safe_config(data):
    run_id = ask(data)
    data.doc.version = 4; data.doc.file_name = "已更新制度.md"; data.session.commit()
    response = data.client.get(f"/api/qa/runs/{run_id}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    saved = response.json()
    item = saved["snapshot"]["context"]["items"][0]
    assert item["document_version"] == 3 and item["vector_generation"] == "generation-3"
    assert item["document_name"] == "制度.md" and item["content"] == "旧版例外条件<script>"
    assert item["parent_id"] == "parent" and len(item["content_sha256"]) == 64
    assert saved["snapshot"]["context"]["rendered_text"] == "完整模型上下文"
    assert saved["response"]["answer"] == "原始回答<script>"
    assert saved["config"]["chat_model"] == "test-model"
    assert "secret-api-key" not in response.text and "file:///" not in response.text
    assert UUID(run_id).hex == saved["request_id"]


def test_http_snapshot_persists_visible_generation_without_offline_trace(data, monkeypatch):
    from backend.app.evaluation.trace import record_answer_generation, record_rag_event
    original = data.client.app.state.answer_service.answer
    def instrumented(*args, **kwargs):
        result = original(*args, **kwargs)
        claims = [dict(text='首稿需要核对例外条件', citations=['1'])]
        record_answer_generation(result, claims, dict(plan_warnings=['缺少辅助计划的覆盖记录：P1']),
            changed=False, validation_errors=[])
        record_rag_event(dict(kind='repair', outcome='not_needed', model_calls=0))
        claims.clear()
        return result
    monkeypatch.setattr(data.client.app.state.answer_service, 'answer', instrumented)
    response = data.client.post('/api/qa', json=dict(question='q', knowledge_base_id=str(data.bases[0].id)))
    assert response.status_code == 200 and 'generation' not in response.json()
    run_id = response.json()['run_id']
    saved = data.client.get(f'/api/qa/runs/{run_id}').json()['snapshot']['generation']
    assert saved['complete']
    first = saved['stages']['answer_generation'][0]
    assert first['claims'][0]['text'] == '首稿需要核对例外条件'
    assert first['coverage']['plan_warnings'] and not first['validation_errors']
    assert saved['stages']['rag_routing'] == [dict(kind='repair', outcome='not_needed', model_calls=0)]
    data.current = data.principals[1]
    assert data.client.get(f'/api/qa/runs/{run_id}').status_code == 404


def test_generation_limits_preserve_evidence_and_request_isolation(data):
    from backend.app.evaluation.trace import record_answer_generation, record_rag_event
    from backend.app.quality.capture import MAX_DIAGNOSTIC_BYTES, MAX_DIAGNOSTIC_EVENTS
    scope = RetrievalScope(data.current.tenant_id, data.bases[0].id)
    with capture_snapshot(scope, 'q') as capture:
        record_context(BuiltContext('已授权正文', [], 6))
        result = AnswerResult(answer='回答', answerable=False, citations=[])
        record_answer_generation(result, [], dict(oversized='中' * MAX_DIAGNOSTIC_BYTES),
            changed=False, validation_errors=[])
        for _ in range(MAX_DIAGNOSTIC_EVENTS + 1):
            record_rag_event(dict(kind='repair', outcome='not_needed', model_calls=0))
        status, saved = capture.freeze()
    assert status == 'captured' and saved['context']['rendered_text'] == '已授权正文'
    assert not saved['generation']['complete']
    assert 'answer_generation' not in saved['generation']['stages']
    assert len(saved['generation']['stages']['rag_routing']) == MAX_DIAGNOSTIC_EVENTS
    with capture_snapshot(scope, 'other') as second:
        assert second.freeze()[1]['generation']['stages'] == {}


def test_feedback_upsert_is_unique_validated_and_audited(data):
    run_id = ask(data)
    first = put(data, run_id)
    assert first.status_code == 200 and first.json()["share_with_admins"] is False
    second = put(data, run_id, rating="down", reason="incomplete", comment="  漏了例外条件  ", share_with_admins=True)
    assert second.status_code == 200 and first.json()["id"] == second.json()["id"]
    assert second.json()["comment"] == "漏了例外条件"
    assert data.session.scalar(select(func.count()).select_from(QAFeedback)) == 1
    assert data.session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "qa.feedback_saved")) == 2
    rows = data.client.get("/api/qa/feedback").json()["items"]
    assert len(rows) == 1 and rows[0]["rating"] == "down"
    assert data.client.get("/api/qa/feedback?rating=up").json()["items"] == []


@pytest.mark.parametrize("change", [dict(rating="down"), dict(reason="incorrect"), dict(rating="sideways"),
                                   dict(comment="x" * 2001), dict(user_id=str(uuid4())), dict(allow_dataset_export=True, share_with_admins=False)])
def test_feedback_rejects_invalid_and_forged_fields(data, change):
    assert put(data, ask(data), **change).status_code == 422
    assert data.session.scalar(select(func.count()).select_from(QAFeedback)) == 0


@pytest.mark.parametrize("identity", ["peer", "outside", "admin"])
def test_private_snapshot_and_feedback_owner_cannot_be_bypassed(data, identity):
    run_id = ask(data)
    data.current = {"peer": data.principals[1], "outside": data.outside, "admin": data.principals[2]}[identity]
    assert data.client.get(f"/api/qa/runs/{run_id}").status_code == 404
    assert put(data, run_id).status_code == 404
    assert data.client.delete(f"/api/qa/runs/{run_id}/feedback").status_code == 404
    assert data.client.get("/api/qa/feedback").json()["items"] == []


def test_shared_cross_kb_snapshot_requires_admin_on_every_original_scope_and_can_be_withdrawn(data):
    run_id = ask(data, all_scopes=True)
    assert put(data, run_id, share_with_admins=True).status_code == 200
    data.current = data.principals[2]
    assert data.client.get("/api/qa/feedback?view=shared").json()["items"] == []
    assert data.client.get(f"/api/qa/runs/{run_id}").status_code == 404
    data.current = data.principals[3]
    assert len(data.client.get("/api/qa/feedback?view=shared").json()["items"]) == 1
    assert data.client.get(f"/api/qa/runs/{run_id}").status_code == 200
    assert put(data, run_id).status_code == 404
    data.current = data.principals[0]
    put(data, run_id, share_with_admins=False)
    data.current = data.principals[3]
    assert data.client.get("/api/qa/feedback?view=shared").json()["items"] == []
    assert data.client.get(f"/api/qa/runs/{run_id}").status_code == 404


def test_revocation_hides_feedback_and_snapshot_from_owner(data):
    run_id = ask(data, all_scopes=True); put(data, run_id)
    grant = data.session.get(KnowledgeBaseUserGrant, (data.current.tenant_id, data.bases[1].id, data.current.user_id))
    data.session.delete(grant); data.session.commit()
    assert data.client.get("/api/qa/feedback").json()["items"] == []
    assert data.client.get(f"/api/qa/runs/{run_id}").status_code == 404
    assert put(data, run_id).status_code == 404


def test_source_deletion_does_not_resurrect_historical_content(data):
    run_id = ask(data); put(data, run_id, share_with_admins=True)
    data.session.delete(data.doc); data.session.commit()
    assert data.client.get(f"/api/qa/runs/{run_id}").status_code == 404
    assert data.client.get("/api/qa/feedback").json()["items"] == []


def test_feedback_can_be_deleted_idempotently(data):
    run_id = ask(data); put(data, run_id)
    for _ in range(2):
        assert data.client.delete(f"/api/qa/runs/{run_id}/feedback").status_code == 204
    assert data.client.get("/api/qa/feedback").json()["items"] == []
    assert data.client.get(f"/api/qa/runs/{run_id}").json()["feedback"] is None


def test_cursor_with_equal_timestamps_is_complete_and_scoped(data):
    for _ in range(3):
        put(data, ask(data))
    for row in data.session.scalars(select(QAFeedback)):
        row.updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    data.session.commit()
    ids, cursor = [], {}
    for _ in range(4):
        body = data.client.get("/api/qa/feedback", params=dict(limit=1, **cursor)).json()
        ids.extend(row["id"] for row in body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 3
    assert data.client.get("/api/qa/feedback?before=2026-01-01T00:00:00").status_code == 422
    assert data.client.get("/api/qa/feedback", params=dict(view="shared", knowledge_base_id=data.bases[0].id)).status_code == 403


def test_conversation_restore_has_run_ids_history_and_delete_cascades(data):
    cid = data.client.post("/api/conversations").json()["id"]
    first = ask(data, all_scopes=True, conversation_id=cid)
    second = ask(data, all_scopes=True, conversation_id=cid, question="它还有例外吗？")
    put(data, first); put(data, second)
    detail = data.client.get(f"/api/conversations/{cid}").json()
    assert [turn["run_id"] for turn in detail["turns"]] == [first, second]
    saved = data.client.get(f"/api/qa/runs/{second}").json()
    assert saved["retrieval_question"] == "独立的追问"
    assert saved["snapshot"]["history"][0]["question"] == "报销有什么条件？"
    assert data.client.delete(f"/api/conversations/{cid}").status_code == 204
    for model in (QARun, QARunScope, QAFeedback):
        assert data.session.scalar(select(func.count()).select_from(model)) == 0


def test_diagnostic_write_failure_preserves_success_and_redacts_exception(data, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("secret-db-password")
    monkeypatch.setattr(qa, "save_run", broken)
    response = data.client.post("/api/qa", json=dict(question="q", knowledge_base_id=str(data.bases[0].id)))
    assert response.status_code == 200 and response.json()["snapshot_status"] == "unavailable"
    assert not response.json().get("run_id")
    assert "secret-db-password" not in caplog.text and "qa.snapshot_failed" in caplog.text


@pytest.mark.parametrize("bad", ["owner", "tenant", "reason"])
def test_database_rejects_feedback_scope_and_reason_forgery(data, bad):
    run_id = ask(data)
    row = dict(tenant_id=data.current.tenant_id, run_id=UUID(run_id), user_id=data.current.user_id,
               rating="down", reason="other", share_with_admins=False)
    if bad == "owner": row["user_id"] = data.principals[1].user_id
    if bad == "tenant": row["tenant_id"] = data.outside.tenant_id
    if bad == "reason": row["reason"] = None
    with pytest.raises(IntegrityError):
        with data.session.begin_nested():
            data.session.add(QAFeedback(**row)); data.session.flush()


def test_capture_rejects_out_of_scope_rendering_and_is_request_local(data):
    scope = RetrievalScope(data.current.tenant_id, data.bases[0].id)
    item = ContextItem(citation_id="x", content="foreign-secret", source="secret-path", document_id=None,
        document_name="secret", section_path="", chunk_id=None, rerank_score=None,
        knowledge_base_id=str(data.bases[1].id))
    with capture_snapshot(scope, "q") as capture:
        record_context(BuiltContext("foreign-secret", [item], 14))
        from backend.app.evaluation.trace import record_answer_generation
        record_answer_generation(AnswerResult(answer='foreign-secret', answerable=False, citations=[]),
            [], {}, changed=False, validation_errors=[])
        status, saved = capture.freeze()
    assert status == "incomplete" and saved["context"]["items"] == []
    assert "foreign-secret" not in str(saved)
    with capture_snapshot(scope, "q") as second:
        assert second.freeze()[0] == "not_observed"


def test_snapshot_toggle_and_missing_instrumentation_are_explicit(data):
    data.client.app.state.settings.qa_snapshots_enabled = False
    response = data.client.post("/api/qa", json={"question": "q", "knowledge_base_id": str(data.bases[0].id)})
    assert response.status_code == 200 and "run_id" not in response.json()
    assert data.session.scalar(select(func.count()).select_from(QARun)) == 0
    data.client.app.state.settings.qa_snapshots_enabled = True
    data.client.app.state.answer_service = SimpleNamespace(answer=lambda *_, **__: AnswerResult(answer="无证据", answerable=False, citations=[]))
    response = data.client.post("/api/qa", json={"question": "q", "knowledge_base_id": str(data.bases[0].id)})
    assert response.json()["snapshot_status"] == "not_observed"


def test_feedback_and_audit_are_one_transaction(data, monkeypatch):
    from backend.app.audit_service import AuditService
    run_id = ask(data)
    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(AuditService, "add", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        put(data, run_id)
    assert data.session.scalar(select(func.count()).select_from(QAFeedback)) == 0


def test_unauthenticated_feedback_endpoints_require_login(data):
    data.client.app.dependency_overrides.pop(get_current_principal)
    assert data.client.get("/api/qa/feedback").status_code == 401


def test_snapshot_savepoint_failure_keeps_completed_conversation(data):
    cid = data.client.post("/api/conversations").json()["id"]
    engine = data.session.get_bind()
    def fail_scope_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO qa_run_scopes"):
            raise RuntimeError("simulated scope write failure")
    event.listen(engine, "before_cursor_execute", fail_scope_insert)
    try:
        response = data.client.post("/api/qa", json={"question": "q", "conversation_id": cid})
    finally:
        event.remove(engine, "before_cursor_execute", fail_scope_insert)
    assert response.status_code == 200 and response.json()["snapshot_status"] == "unavailable"
    assert data.session.scalar(select(func.count()).select_from(QARun)) == 0
    detail = data.client.get(f"/api/conversations/{cid}").json()
    assert len(detail["turns"]) == 1 and detail["turns"][0]["run_id"] is None


def test_model_usage_snapshot_uses_only_safe_fields_and_preserves_unknowns(data):
    from backend.app.model_usage import current_usage_context
    original = data.client.app.state.answer_service.answer
    def answer(*args, **kwargs):
        current_usage_context().events.append(dict(stage="answer", provider="fake", model="fake",
            total_tokens=None, estimated_cost=None, details={"authorization": "must-not-leak"}))
        return original(*args, **kwargs)
    data.client.app.state.answer_service = SimpleNamespace(answer=answer)
    response = data.client.get(f"/api/qa/runs/{ask(data)}")
    row = response.json()["snapshot"]["model_usage"][0]
    assert row["total_tokens"] is None and row["estimated_cost"] is None
    assert "must-not-leak" not in response.text
