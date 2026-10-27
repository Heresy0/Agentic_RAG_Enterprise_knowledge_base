"""Migration and concurrent feedback checks in a disposable PostgreSQL schema."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import os
import re
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.api.feedback import FeedbackInput
from backend.app.config import required_env
from backend.app.db.conversation_models import Conversation
from backend.app.db.models import KnowledgeBase, Tenant, User
from backend.app.db.quality_models import QARun, QARunScope, QAFeedback
from backend.app.quality.service import FeedbackStore
from backend.app.security.principal import Principal

pytestmark = pytest.mark.integration


def test_postgres_migration_concurrent_upsert_constraints_and_cascade():
    if os.getenv("RUN_QA_FEEDBACK_INTEGRATION_TESTS") != "1":
        pytest.skip("QA feedback PostgreSQL integration tests not enabled")
    schema = "qa_feedback_test_" + uuid4().hex
    assert re.fullmatch(r"qa_feedback_test_[0-9a-f]{32}", schema)
    control = create_engine(required_env("DATABASE_URL"))
    engine = create_engine(required_env("DATABASE_URL"), connect_args={"options": f"-csearch_path={schema}"})
    migration = importlib.import_module("migrations.versions.f4b6d8e0a215_qa_snapshots_feedback")
    created = False
    try:
        with control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        with engine.begin() as connection:
            for table in (Tenant.__table__, User.__table__, KnowledgeBase.__table__, Conversation.__table__):
                table.create(connection)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            inspector = inspect(connection)
            for model in (QARun, QARunScope, QAFeedback):
                assert {column["name"] for column in inspector.get_columns(model.__tablename__)} == set(model.__table__.columns.keys())
        with Session(engine, expire_on_commit=False) as session:
            tenant = Tenant(name="isolated-test"); session.add(tenant); session.flush()
            user = User(tenant_id=tenant.id, name="Owner", external_subject=uuid4().hex)
            peer = User(tenant_id=tenant.id, name="Peer", external_subject=uuid4().hex)
            session.add_all([user, peer]); session.flush()
            conversation = Conversation(tenant_id=tenant.id, user_id=user.id)
            session.add(conversation); session.flush()
            run = QARun(id=uuid4(), tenant_id=tenant.id, user_id=user.id, request_id=uuid4().hex,
                conversation_id=conversation.id, turn_index=1, question="q", retrieval_question="q",
                response_json={"answer": "a"}, snapshot_json={}, snapshot_status="not_observed", config_json={})
            session.add(run); session.flush()
            session.add(QARunScope(tenant_id=tenant.id, run_id=run.id, knowledge_base_id=uuid4()))
            session.commit()
            principal = Principal(user.id, tenant.id, user.external_subject)
            run_id, peer_id, cid = run.id, peer.id, conversation.id
        barrier = Barrier(2)

        def writer(rating):
            with Session(engine) as session:
                barrier.wait(timeout=10)
                run = session.scalar(select(QARun).where(QARun.id == run_id).with_for_update())
                row = FeedbackStore(session, principal).put(run, FeedbackInput(
                    rating=rating, reason="other" if rating == "down" else None))
                identifier = row.id
                session.commit()
                return identifier

        with ThreadPoolExecutor(max_workers=2) as workers:
            first, second = list(workers.map(writer, ("up", "down")))
        assert first == second
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(QAFeedback)) == 1
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(QAFeedback(tenant_id=principal.tenant_id, run_id=run_id, user_id=peer_id,
                        rating="up", share_with_admins=False)); session.flush()
            session.delete(session.get(Conversation, cid)); session.commit()
            for model in (QARun, QARunScope, QAFeedback):
                assert session.scalar(select(func.count()).select_from(model)) == 0
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                migration.upgrade()
    finally:
        engine.dispose()
        if created:
            with control.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()
