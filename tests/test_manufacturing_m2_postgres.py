"""Real PostgreSQL JSON predicates, BM25 and concurrent metadata revision CAS."""
import os
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from manufacturing_m2_helpers import add_source, context, scopes, seed_catalog
from backend.app.api.document_review import update_manufacturing_metadata, update_publication, PublicationInput
from backend.app.db.models import AuditEvent, KnowledgeBase, KnowledgeBaseUserGrant, KnowledgeDocument, Tenant, User
from backend.app.knowledge.chunk_repository import ChunkRepository
from backend.app.knowledge.keyword_repository import PgKeywordRepository
from backend.app.manufacturing.applicability import ApplicabilityResolver
from backend.app.manufacturing.metadata import MetadataInput
from backend.app.manufacturing.scope import ManufacturingScopeChanged
from backend.app.security.principal import Principal

pytestmark = pytest.mark.integration


@pytest.fixture
def pg():
    value = os.environ.get("MANUFACTURING_TEST_DATABASE_URL")
    if not value:
        pytest.skip("explicit independent M2 test database not configured")
    url = make_url(value)
    assert url.host in ("127.0.0.1", "localhost", "::1")
    assert re.fullmatch(r"manufacturing_m2_test_[a-f0-9]{12}", url.database or ""), "refuse business database"
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    factory = sessionmaker(engine, expire_on_commit=False)
    tenant, kb, user = uuid4(), uuid4(), uuid4()
    try:
        with factory() as session:
            session.add(Tenant(id=tenant, name="M2 isolated tenant")); session.flush()
            session.add_all([User(id=user, tenant_id=tenant, external_subject=user.hex, name="reviewer"),
                KnowledgeBase(id=kb, tenant_id=tenant, name="M2 reviewed catalog", visibility="restricted")]); session.flush()
            session.add(KnowledgeBaseUserGrant(tenant_id=tenant, knowledge_base_id=kb, user_id=user, permission="editor"))
            session.commit()
            docs = seed_catalog(session, tenant, kb)
        yield factory, tenant, kb, Principal(user, tenant, user.hex), docs
    finally:
        with engine.begin() as connection:
            connection.execute(delete(Tenant).where(Tenant.id == tenant))
        engine.dispose()


def selection(pg, session, wo="WO-018"):
    _, tenant, kb, principal, _ = pg
    scope, business = scopes(tenant, kb, principal.user_id)
    return ApplicabilityResolver().resolve(context=context(wo, tenant_id=tenant), scope=scope, business_scope=business, session=session)


@pytest.mark.parametrize("wo,state,rev", [("WO-017", "applicable", "2.0"), ("WO-018", "applicable", "1.0"),
    ("WO-019", "applicable", "2.0"), ("WO-020", "needs_context", None), ("WO-021", "no_evidence", None), ("WO-024", "conflict", None)])
def test_postgres_selection_and_sql_gates(pg, wo, state, rev):
    factory, _, _, _, docs = pg
    with factory() as session:
        decision = selection(pg, session, wo)
        assert (decision.state, decision.selected_business_revision) == (state, rev)
        rows = ChunkRepository(session).list_chunks(scope=decision.scope)
        if state != "applicable": assert rows == []
        else:
            ids = {row.document_id for row in rows}
            assert docs["old" if rev == "1.0" else "new"][0].id in ids
            assert docs["new" if rev == "1.0" else "old"][0].id not in ids


def test_postgres_bm25_filters_wrong_version_before_top_one(pg):
    factory, tenant, kb, _, docs = pg
    with factory() as session:
        decision = selection(pg, session)
        rows = PgKeywordRepository(session).search("制造", scope=decision.scope, limit=1)
        assert len(rows) == 1
        assert rows[0].document_id in decision.scope.manufacturing.document_ids
        assert rows[0].document_id != docs["new"][0].id
        blocked = selection(pg, session, "WO-024")
        assert PgKeywordRepository(session).search("制造", scope=blocked.scope, limit=1) == []


def test_concurrent_metadata_save_has_exactly_one_commit_and_audit(pg):
    factory, tenant, kb, principal, docs = pg
    doc_id, data = docs["old"][0].id, docs["old"][2]
    barrier = Barrier(2)
    value = MetadataInput(expected_document_version=1, expected_revision=2, state="draft", metadata=data, review_note="并发修改适用性")
    def write():
        with factory() as session:
            barrier.wait(timeout=10)
            try:
                update_manufacturing_metadata(kb, doc_id, value, Request({"type": "http"}), Response(), principal, session)
                return 200
            except HTTPException as exc:
                session.rollback(); return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: write(), range(2)))
    assert sorted(statuses) == [200, 409]
    with factory() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        assert doc.metadata_json["manufacturing"]["revision"] == 3
        assert doc.metadata_json["publication"]["state"] == "draft"
        assert len(session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant,
            AuditEvent.action == "document.manufacturing_metadata_changed")).all()) == 1


def test_postgres_review_publish_and_withdraw_invalidates_an_open_session(pg):
    factory, tenant, kb, principal, docs = pg
    doc_id = docs["ecn"][0].id
    with factory() as reader:
        decision = selection(pg, reader)
        assert ChunkRepository(reader).has_ready_chunks(scope=decision.scope)
        with factory() as writer:
            update_publication(kb, doc_id, PublicationInput(expected_version=1, expected_revision=1,
                state="withdrawn", review_note="撤回选版依据"), Request({"type": "http"}), Response(), principal, writer)
        with pytest.raises(ManufacturingScopeChanged):
            ChunkRepository(reader).has_ready_chunks(scope=decision.scope)


def test_postgres_save_review_publication_use_current_revision(pg):
    factory, tenant, kb, principal, docs = pg
    doc_id, data = docs["old"][0].id, docs["old"][2]
    with factory() as session:
        args = dict(expected_document_version=1, expected_revision=2, state="draft", metadata=data, review_note="原文核对")
        draft = update_manufacturing_metadata(kb, doc_id, MetadataInput(**args), Request({"type": "http"}), Response(), principal, session)
        args.update(expected_revision=3, state="reviewed")
        reviewed = update_manufacturing_metadata(kb, doc_id, MetadataInput(**args), Request({"type": "http"}), Response(), principal, session)
        assert reviewed["revision"] == 4
        update_publication(kb, doc_id, PublicationInput(expected_version=1,
            expected_revision=reviewed["document"]["publication"]["revision"], state="published", review_note="批准发布"),
            Request({"type": "http"}), Response(), principal, session)
        assert selection(pg, session).selected_business_revision == "1.0"
