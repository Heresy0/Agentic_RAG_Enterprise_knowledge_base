"""Isolated PostgreSQL migration, HTTP transaction, privacy and usage acceptance."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib
import os
import re
from threading import Event
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import sessionmaker

from manufacturing_m3_helpers import configure
from test_manufacturing_m3_api import ask, create
from test_document_upload_api import _set_principal
from backend.app.db.models import Tenant, User, KnowledgeBase, KnowledgeBaseUserGrant, AuditEvent
from backend.app.db.conversation_models import Conversation, ConversationTurn
from backend.app.db.quality_models import QARun
from backend.app.db.business_models import BusinessScopeGrant, ConversationBusinessBinding, QARunBusinessScope
from backend.app.db.model_usage_models import ModelUsageEvent
from backend.app.security.principal import Principal
from backend.app.quality.reviews import ReviewStore
from backend.app.quality.service import ObservationNotFound

pytestmark = pytest.mark.integration


def pg_context(monkeypatch, *, phase="m3"):
    value = os.environ.get("MANUFACTURING_TEST_DATABASE_URL")
    if not value: pytest.skip("explicit independent M3 database not configured")
    url = make_url(value)
    assert phase in ("m3", "m4")
    assert url.host in ("127.0.0.1", "localhost", "::1") and re.fullmatch(r"manufacturing_" + phase + r"_test_[a-f0-9]{12}", url.database or "")
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    factory = sessionmaker(engine, expire_on_commit=False)
    tenant, kb, user, viewer = uuid4(), uuid4(), uuid4(), uuid4()
    h = None
    try:
        with factory() as session:
            session.add(Tenant(id=tenant, name="M3 isolated tenant")); session.flush()
            session.add_all([User(id=user, tenant_id=tenant, external_subject=user.hex, name="owner"),
                User(id=viewer, tenant_id=tenant, external_subject=viewer.hex, name="reviewer"),
                KnowledgeBase(id=kb, tenant_id=tenant, name="M3 documents", visibility="restricted")]); session.flush()
            session.add_all([KnowledgeBaseUserGrant(tenant_id=tenant, knowledge_base_id=kb, user_id=identifier, permission=permission)
                for identifier, permission in ((user, "editor"), (viewer, "admin"))]); session.commit()
        app = FastAPI(); app.state.database_session_factory = factory
        c = SimpleNamespace(app=app, session_factory=factory, tenant_id=tenant, knowledge_base_id=kb,
            editor=Principal(user, tenant, user.hex), viewer=Principal(viewer, tenant, viewer.hex))
        h = configure(c, monkeypatch); h.engine = engine
        yield h
    finally:
        if h is not None: asyncio.run(h.connector.aclose())
        with engine.begin() as connection: connection.execute(delete(Tenant).where(Tenant.id == tenant))
        engine.dispose()


@pytest.fixture
def pg_harness(monkeypatch):
    yield from pg_context(monkeypatch, phase="m3")


def test_postgres_head_has_nullable_invalidation_and_old_business_bindings_stay_readable(pg_harness):
    h = pg_harness
    with h.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "e9b2d4f60713"
        column = next(row for row in inspect(connection).get_columns("conversation_business_bindings") if row["name"] == "invalidated_at")
        assert column["nullable"]
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
        with h.c.session_factory() as session:
            binding = session.get(ConversationBusinessBinding, (h.c.tenant_id, UUID(identifier)))
            assert binding.invalidated_at is None and binding.source_revision == "r1"
            assert binding.operation_id is None and binding.job_card_id is None
            assert binding.batch_id is None and binding.step_id is None


def test_postgres_execution_binding_constraints_and_downgrade_preserve_selection(pg_harness):
    h = pg_harness
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
    with h.engine.begin() as connection:
        params = dict(tenant=h.c.tenant_id, conversation=UUID(identifier))
        for expression in ("job_card_id='JC-A'", "operation_id=''", "operation_id=' ROW-A '", "operation_id='ROW-A', job_card_id=' '"):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text('UPDATE conversation_business_bindings SET ' + expression +
                    ' WHERE tenant_id=:tenant AND conversation_id=:conversation'), params)
        connection.execute(text("UPDATE conversation_business_bindings SET operation_id='ROW-A', job_card_id='JC-A' "
            'WHERE tenant_id=:tenant AND conversation_id=:conversation'), params)
        migration = importlib.import_module('migrations.versions.f2b8c0a961d3_execution_selection_binding')
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match='拒绝丢失业务边界'):
                migration.downgrade()
        row = connection.execute(text('SELECT operation_id, job_card_id FROM conversation_business_bindings '
            'WHERE tenant_id=:tenant AND conversation_id=:conversation'), params).one()
        assert tuple(row) == ('ROW-A', 'JC-A')


@pytest.mark.parametrize("at,status", [(2, 409), (3, 409)])
def test_source_revision_failure_rolls_back_answer_but_keeps_real_usage_accounting(pg_harness, at, status):
    h = pg_harness
    def change(source, count):
        if count == at: source.records["WO-018"]["source_revision"] = "r2"
    h.source.hook = change
    with TestClient(h.c.app) as client:
        response = ask(client, h); assert response.status_code == status, response.text
    with h.c.session_factory() as session:
        assert session.scalars(select(QARun).where(QARun.tenant_id == h.c.tenant_id)).all() == []
        events = session.scalars(select(ModelUsageEvent).where(ModelUsageEvent.tenant_id == h.c.tenant_id)).all()
        assert len(events) == (1 if at == 3 else 0)
        assert all(event.status != "pending" for event in events)


@pytest.mark.parametrize("error,status", [(RuntimeError, 500), (SQLAlchemyError, 503)])
@pytest.mark.parametrize("operation", [False, True])
def test_audit_failure_is_atomic_with_turn_snapshot_and_business_scope(pg_harness, monkeypatch, error, status, operation):
    h = pg_harness
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
        original = AuditEvent
        from backend.app.api import manufacturing as api
        original_add = api.AuditService.add
        def fail(self, **kwargs):
            if kwargs["action"] == "manufacturing.qa_queried" and kwargs["outcome"] == "success": raise error("test audit unavailable")
            return original_add(self, **kwargs)
        monkeypatch.setattr(api.AuditService, "add", fail)
        response = ask(client, h, conversation_id=identifier,
            **({"question": "请系统放行这张工单"} if operation else {}))
        assert response.status_code == status, response.text
    with h.c.session_factory() as session:
        assert session.get(Conversation, UUID(identifier)).turn_count == 0
        assert session.scalars(select(ConversationTurn).where(ConversationTurn.tenant_id == h.c.tenant_id)).all() == []
        assert session.scalars(select(QARun).where(QARun.tenant_id == h.c.tenant_id)).all() == []
        assert session.scalars(select(QARunBusinessScope).where(QARunBusinessScope.tenant_id == h.c.tenant_id)).all() == []
        assert len(session.scalars(select(ModelUsageEvent).where(ModelUsageEvent.tenant_id == h.c.tenant_id)).all()) == (0 if operation else 1)


def test_readonly_operation_refusal_saves_turn_and_snapshot_without_provider_usage(pg_harness):
    h = pg_harness
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
        h.source.reads.clear()
        response = ask(client, h, conversation_id=identifier, question="请系统放行这张工单")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["response_type"] == "refuse" and body["answerable"] is False
    assert len(h.source.reads) == 3 and not h.generation.calls
    with h.c.session_factory() as session:
        assert session.get(Conversation, UUID(identifier)).turn_count == 1
        assert len(session.scalars(select(ConversationTurn).where(ConversationTurn.tenant_id == h.c.tenant_id)).all()) == 1
        run = session.get(QARun, UUID(body["run_id"]))
        assert run.response_json["response_type"] == "refuse"
        assert len(session.scalars(select(QARunBusinessScope).where(QARunBusinessScope.tenant_id == h.c.tenant_id)).all()) == 1
        assert session.scalars(select(ModelUsageEvent).where(ModelUsageEvent.tenant_id == h.c.tenant_id)).all() == []


def test_one_bound_conversation_cannot_commit_two_concurrent_turns(pg_harness):
    h = pg_harness; entered, release = Event(), Event()
    def block(active):
        entered.set(); assert release.wait(15), "test generation release timed out"
    h.generation.hook = block
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(ask, client, h, conversation_id=identifier)
            try:
                assert entered.wait(10)
                response = ask(client, h, conversation_id=identifier)
                assert response.status_code == 409 and response.json()["detail"]["error_code"] == "conversation_busy"
            finally: release.set()
            completed = pending.result(timeout=20); assert completed.status_code == 200, completed.text
    with h.c.session_factory() as session:
        assert session.get(Conversation, UUID(identifier)).turn_count == 1
        assert len(session.scalars(select(ConversationTurn).where(ConversationTurn.tenant_id == h.c.tenant_id)).all()) == 1
        assert len(session.scalars(select(QARunBusinessScope).where(QARunBusinessScope.tenant_id == h.c.tenant_id)).all()) == 1


def test_shared_snapshot_requires_business_review_as_well_as_kb_admin(pg_harness):
    h = pg_harness
    with TestClient(h.c.app) as client:
        response = ask(client, h); assert response.status_code == 200, response.text
        run_id = response.json()["run_id"]
        share = client.put(f"/api/qa/runs/{run_id}/feedback", json=dict(rating="down", reason="incorrect", comment="M3 privacy test",
            share_with_admins=True, allow_dataset_export=False)); assert share.status_code == 200
        _set_principal(h.c, h.c.viewer)
        assert client.get(f"/api/qa/runs/{run_id}").status_code == 404
        with h.c.session_factory() as session:
            session.add(BusinessScopeGrant(tenant_id=h.c.tenant_id, user_id=h.c.viewer.user_id,
                source_system_id="mock-mes-hc", plant_code="HC-P01", permission="view")); session.commit()
        assert client.get(f"/api/qa/runs/{run_id}").status_code == 404
        with h.c.session_factory() as session:
            session.add(BusinessScopeGrant(tenant_id=h.c.tenant_id, user_id=h.c.viewer.user_id,
                source_system_id="mock-mes-hc", plant_code="HC-P01", permission="review")); session.commit()
        assert client.get(f"/api/qa/runs/{run_id}").status_code == 200
        with h.c.session_factory() as session:
            store = ReviewStore(session, h.c.viewer)
            run, feedback = store.require_run(UUID(run_id))
            assert store.eligibility(None, run, feedback) == (False, "not_approved")
            session.execute(delete(BusinessScopeGrant).where(BusinessScopeGrant.user_id == h.c.viewer.user_id,
                BusinessScopeGrant.permission == "view")); session.commit()
        assert client.get(f"/api/qa/runs/{run_id}").status_code == 404


def test_migration_preserves_old_rows_and_blocks_downgrade_after_invalidation(pg_harness):
    h = pg_harness; schema = "m3_migration_"+uuid4().hex
    assert re.fullmatch(r"m3_migration_[a-f0-9]{32}", schema)
    old = importlib.import_module("migrations.versions.d8f0b2c43569_business_work_order_access")
    new = importlib.import_module("migrations.versions.e9a1c3d54670_manufacturing_conversation_invalidation")
    with h.engine.begin() as connection: connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(h.engine.url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as connection:
            for model in (Tenant, User, KnowledgeBase, Conversation, QARun): model.__table__.create(connection)
            with Operations.context(MigrationContext.configure(connection)):
                old.upgrade()
                tenant, user, conversation = uuid4(), uuid4(), uuid4()
                connection.execute(Tenant.__table__.insert().values(id=tenant, name="legacy"))
                connection.execute(User.__table__.insert().values(id=user, tenant_id=tenant, external_subject=user.hex, name="legacy"))
                connection.execute(Conversation.__table__.insert().values(id=conversation, tenant_id=tenant, user_id=user, title="preserved"))
                connection.execute(text("INSERT INTO conversation_business_bindings (tenant_id,conversation_id,source_system_id,plant_code,work_order_id,source_revision,payload_sha256) VALUES (:tenant,:conversation,'mock-mes-hc','HC-P01','WO-018','r1',:hash)"),
                    dict(tenant=tenant, conversation=conversation, hash="a"*64))
                new.upgrade(); new.downgrade(); new.upgrade()
                assert connection.scalar(text("SELECT source_revision FROM conversation_business_bindings")) == "r1"
                assert connection.scalar(text("SELECT invalidated_at FROM conversation_business_bindings")) is None
                connection.execute(text("UPDATE conversation_business_bindings SET invalidated_at=now()"))
                with pytest.raises(RuntimeError, match="拒绝"): new.downgrade()
    finally:
        engine.dispose()
        with h.engine.begin() as connection: connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
