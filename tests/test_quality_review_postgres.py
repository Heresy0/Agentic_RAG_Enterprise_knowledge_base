"""Real migrations, legacy consent and optimistic concurrency in an isolated schema."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import os
import re
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

from backend.app.api.feedback import FeedbackInput
from backend.app.config import required_env
from backend.app.db.conversation_models import Conversation
from backend.app.db.models import AuditEvent, Department, DepartmentMembership, KnowledgeBase, KnowledgeBaseDepartmentGrant, KnowledgeBaseUserGrant, KnowledgeDocument, Tenant, User
from backend.app.db.quality_models import QARun, QARunScope, QAFeedback
from backend.app.db.review_models import QualityCase, QualityCaseRevision, QualityDatasetVersion
from backend.app.quality.review_schemas import DatasetInput, ReviewInput
from backend.app.quality.reviews import ReviewConflict, ReviewStore
from backend.app.security.principal import Principal

pytestmark = pytest.mark.integration


def test_postgres_legacy_consent_concurrent_reviews_and_export():
    if os.getenv("RUN_QA_FEEDBACK_INTEGRATION_TESTS") != "1":
        pytest.skip("QA feedback PostgreSQL integration tests not enabled")
    schema = "quality_review_test_" + uuid4().hex
    assert re.fullmatch(r"quality_review_test_[0-9a-f]{32}", schema)
    url = required_env("DATABASE_URL")
    control = create_engine(url)
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    first = importlib.import_module("migrations.versions.f4b6d8e0a215_qa_snapshots_feedback")
    migration = importlib.import_module("migrations.versions.a5c7e9f10236_quality_reviews")
    created = False
    try:
        with control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        with engine.begin() as connection:
            for model in (Tenant, User, Department, KnowledgeBase, DepartmentMembership, KnowledgeBaseDepartmentGrant,
                          KnowledgeBaseUserGrant, Conversation, KnowledgeDocument, AuditEvent):
                model.__table__.create(connection)
            with Operations.context(MigrationContext.configure(connection)):
                first.upgrade()
        tenant_id, user_id, kb_id, run_id, doc_id = (uuid4() for _ in range(5))
        with engine.begin() as connection:
            connection.execute(Tenant.__table__.insert().values(id=tenant_id, name="isolated"))
            connection.execute(User.__table__.insert().values(id=user_id, tenant_id=tenant_id, name="reviewer", external_subject="reviewer"))
            connection.execute(KnowledgeBase.__table__.insert().values(id=kb_id, tenant_id=tenant_id, name="KB", visibility="restricted"))
            connection.execute(KnowledgeBaseUserGrant.__table__.insert().values(tenant_id=tenant_id, user_id=user_id, knowledge_base_id=kb_id, permission="admin"))
            connection.execute(KnowledgeDocument.__table__.insert().values(id=doc_id, tenant_id=tenant_id, knowledge_base_id=kb_id,
                source_id="source", file_name="policy.md", storage_uri="private", mime_type="text/markdown", content_hash="a" * 64, status="ready", version=1))
            connection.execute(QARun.__table__.insert().values(id=run_id, tenant_id=tenant_id, user_id=user_id, request_id=uuid4().hex,
                question="q", retrieval_question="q", response_json={"answer": "old", "citations": []}, config_json={}, snapshot_status="captured",
                snapshot_json={"context": {"items": [dict(citation_id="1", content="source fact", document_id=str(doc_id), knowledge_base_id=str(kb_id),
                    document_name="policy.md", document_version=1, content_sha256=hashlib.sha256(b"source fact").hexdigest())]}}))
            connection.execute(QARunScope.__table__.insert().values(tenant_id=tenant_id, run_id=run_id, knowledge_base_id=kb_id))
            connection.execute(text("INSERT INTO qa_feedback (id, tenant_id, run_id, user_id, rating, reason, share_with_admins) VALUES (:id,:tenant,:run,:user,'down','incomplete',true)"),
                               dict(id=uuid4(), tenant=tenant_id, run=run_id, user=user_id))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            for model in (QualityCase, QualityCaseRevision, QualityDatasetVersion):
                assert {c["name"] for c in inspect(connection).get_columns(model.__tablename__)} == set(model.__table__.columns.keys())
        principal = Principal(user_id, tenant_id, "reviewer")
        with Session(engine, expire_on_commit=False) as session:
            assert session.scalar(select(QAFeedback)).allow_dataset_export is False
            store = ReviewStore(session, principal)
            run, _ = store.require_run(run_id)
            store.feedback_store.put(run, FeedbackInput(rating="down", reason="incomplete", share_with_admins=True, allow_dataset_export=True))
            case, _, _, _ = store.open_case(run_id); case_id = case.id; session.commit()
        barrier = Barrier(2)

        def update(reason):
            with Session(engine) as session:
                barrier.wait(timeout=10)
                try:
                    ReviewStore(session, principal).update_case(case_id, ReviewInput(expected_revision=1, review_status="confirmed",
                        sample_status="draft", root_cause="generation", review_reason=reason))
                    session.commit(); return "saved"
                except ReviewConflict:
                    session.rollback(); return "conflict"

        with ThreadPoolExecutor(max_workers=2) as workers:
            assert sorted(workers.map(update, ("first", "second"))) == ["conflict", "saved"]
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(QualityCaseRevision)) == 2
            store = ReviewStore(session, principal)
            case, _, _ = store.update_case(case_id, ReviewInput(expected_revision=2, review_status="confirmed", sample_status="approved",
                root_cause="generation", review_reason="verified", evaluation_question="q", expected_answerable=True, reference_answer="source fact", required_facts=["source fact"],
                evidence=[dict(citation_id="1", quote="source fact")]))
            dataset = store.create_dataset(DatasetInput(name="regression", case_ids=[case.id])); session.commit()
            content = store.dataset_content(dataset)
            assert hashlib.sha256(content).hexdigest() == dataset.manifest_json["samples_sha256"]
            session.rollback()
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade(); migration.upgrade()
            assert connection.scalar(text("SELECT count(*) FROM quality_cases")) == 0
    finally:
        engine.dispose()
        if created:
            with control.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()
