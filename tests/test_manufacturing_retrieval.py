"""Every evidence path carries the reviewed manufacturing fence."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql

from test_manufacturing_applicability import catalog, resolve
from test_document_upload_api import api_context
from test_retrieval_service import FakeEmbeddings, FakeReranker
from fake_vector_store import FakeVectorStore
from manufacturing_m2_helpers import add_source, reviewed_update
from backend.app.db.models import DocumentChunk, DocumentParent
from backend.app.evaluation.trace import capture_trace, record_scope
from backend.app.knowledge.chunk_repository import ChunkRepository
from backend.app.knowledge.evidence_coordinator import EvidenceCoordinator
from backend.app.knowledge.evidence_selection import select_evidence
from backend.app.knowledge.keyword_repository import PgKeywordRepository
from backend.app.knowledge.milvus_retriever import stored_chunk_to_document
from backend.app.knowledge.milvus_store import MilvusVectorStore, make_vector_records
from backend.app.knowledge.retrieval_service import RetrievalService
from backend.app.manufacturing.metadata import ManufacturingMetadata, SourceAnchor, digest, validate_anchors
from backend.app.manufacturing.scope import ManufacturingScopeChanged


def service_and_store(session, docs, *, parent=False):
    store = FakeVectorStore()
    for doc, chunk, _ in docs.values():
        store.upsert(make_vector_records([chunk], [[1.0]+[0.0]*1023]))
    service = RetrievalService(SimpleNamespace(rag_parent_context_enabled=parent, rag_rerank_top_k=3),
        embeddings=FakeEmbeddings(), reranker=FakeReranker(calls=[]), vector_store=store)
    return service, store


@pytest.mark.parametrize("parent", [False, True])
def test_deciding_sql_sources_survive_empty_vector_keyword_top_k_and_parent_refresh(catalog, monkeypatch, parent):
    c, session, docs = catalog; decision = resolve(c, session); scope = decision.scope
    service, store = service_and_store(session, docs, parent=parent)
    store.records.clear()  # Deciding ECN is deliberately absent from top-k.
    monkeypatch.setattr(PgKeywordRepository, "search", lambda *a, **kw: [])
    with capture_trace() as trace:
        rows = service.search("2026年9月28日的工单，按最新2.0回答？", scope=scope, session=session)
    ids = {row.metadata["document_id"] for row in rows}
    assert {str(docs[key][0].id) for key in ("old", "ecn", "qc")} <= ids
    assert str(docs["new"][0].id) not in ids
    assert trace.stages["manufacturing_rule_evidence"]
    assert all(row.metadata["manufacturing_required"] for row in rows)
    # Even a tool that drops its entire pool cannot erase required evidence.
    assert {row.metadata["document_id"] for row in service.refresh_evidence([], scope=scope, session=session)} == ids
    assert all(row.metadata["policy_selection"] == "reviewed_manufacturing_scope" for row in select_evidence("2020年", rows, scope=scope))


def test_vector_and_keyword_filters_are_applied_before_limit(catalog):
    c, session, docs = catalog; scope = resolve(c, session).scope
    transport = Mock(); transport.post.return_value.json.return_value = {"code": 0, "data": []}
    store = MilvusVectorStore(SimpleNamespace(), transport=transport)
    store.search(scope=scope, generations=[docs["old"][1].vector_generation], vector=[1.0]+[0.0]*1023, limit=1)
    body = transport.post.call_args.kwargs["json"]
    assert "document_id in" in body["filter"]
    assert str(docs["old"][0].id) in body["filter"] and str(docs["new"][0].id) not in body["filter"]
    statement = PgKeywordRepository.search_statement(scope=scope)
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}))
    assert "documents.version =" in sql and "documents.content_hash =" in sql
    assert "manufacturing" in str(statement.compile().params)
    assert sql.index("documents.version =") < sql.index("ORDER BY") < sql.index("LIMIT")
    assert str(docs["new"][0].id) not in map(str, statement.compile().params.values())


def test_current_parent_neighbor_companion_and_agent_merge_exclude_wrong_version_and_work_order(catalog):
    c, session, docs = catalog; scope = resolve(c, session).scope
    repo = ChunkRepository(session)
    for key in ("new", "event17"):
        doc, chunk, _ = docs[key]
        expected = {chunk.id: dict(document_id=str(doc.id), knowledge_base_id=str(c.knowledge_base_id), vector_generation=chunk.vector_generation)}
        assert repo.current_chunks(scope=scope, expected=expected) == []
        assert repo.parent_chunks(scope=scope, expected=expected) == []
        assert repo.neighboring_chunks(scope=scope, chunk_id=chunk.id, generation=chunk.vector_generation, document_id=doc.id) == []
        assert repo.candidate_companions(scope=scope, document_ids=[doc.id]) == []
        assert repo.candidate_policies(scope=scope, document_ids=[doc.id]) == {}
    from backend.app.security.retrieval_scope import RetrievalScope
    ordinary = RetrievalScope(c.tenant_id, c.knowledge_base_id)
    all_rows = [stored_chunk_to_document(row) for row in repo.list_chunks(scope=ordinary)]
    coordinator = EvidenceCoordinator(SimpleNamespace(), object(), decider=object())
    merged = coordinator._merge([], all_rows, scope)
    assert all(scope.contains_document(row.metadata) for row in merged)
    assert str(docs["new"][0].id) not in {row.metadata["document_id"] for row in merged}


@pytest.mark.parametrize("change", ["withdraw", "metadata", "source", "new_notice"])
@pytest.mark.parametrize("path", ["vector", "keyword", "current", "parent", "neighbor", "companion", "refresh"])
def test_every_path_invalidates_selection_when_deciding_catalog_or_source_changes(catalog, change, path):
    c, session, docs = catalog; scope = resolve(c, session).scope
    service, store = service_and_store(session, docs)
    doc, chunk, data = docs["old"]
    rows = ChunkRepository(session).list_chunks(scope=scope)
    expected = {row.chunk_id: row.metadata for row in rows}
    ecn, ecn_chunk, ecn_data = docs["ecn"]
    if change == "withdraw":
        ecn.metadata_json = {**ecn.metadata_json, "publication": {"state": "withdrawn", "revision": 2}}
    elif change == "metadata":
        reviewed_update(session, ecn, ManufacturingMetadata.model_validate({**ecn_data.model_dump(), "archived": True}))
    elif change == "source": ecn_chunk.content = "不可继续使用旧版的原文。"
    else: add_source(session, tenant=c.tenant_id, kb=c.knowledge_base_id, code="NEW-NOTICE", role="change_notice")
    session.commit()
    repo = ChunkRepository(session, store)
    actions = dict(vector=lambda: repo.search(scope=scope, query_embedding=[1.0]+[0.0]*1023),
        keyword=lambda: PgKeywordRepository(session).search("制造", scope=scope),
        current=lambda: repo.current_chunks(scope=scope, expected=expected),
        parent=lambda: repo.parent_chunks(scope=scope, expected=expected),
        neighbor=lambda: repo.neighboring_chunks(scope=scope, chunk_id=chunk.id, generation=chunk.vector_generation, document_id=doc.id),
        companion=lambda: repo.candidate_companions(scope=scope, document_ids=[doc.id]),
        refresh=lambda: service.refresh_evidence([stored_chunk_to_document(row) for row in rows], scope=scope, session=session))
    with pytest.raises(ManufacturingScopeChanged): actions[path]()


def test_parent_bound_rule_quote_is_sql_verified_and_remains_required(catalog):
    c, session, docs = catalog; doc, chunk, data = docs["ecn"]
    text = "变更通知完整章节\n"+chunk.content+"\n批准签署齐全。"
    parent = DocumentParent(id=digest("parent"+chunk.id), tenant_id=c.tenant_id, knowledge_base_id=c.knowledge_base_id,
        document_id=doc.id, vector_generation=chunk.vector_generation, document_version=1,
        document_content_hash=doc.content_hash, first_chunk_index=0, last_chunk_index=0, content=text,
        content_hash=digest(text), metadata_json={"section_path": "变更通知"})
    session.add(parent); chunk.parent_id = parent.id
    anchor = SourceAnchor(chunk_id=chunk.id, vector_generation=chunk.vector_generation, chunk_content_hash=chunk.content_hash,
        parent_id=parent.id, parent_content_hash=parent.content_hash, quote=text, quote_sha256=digest(text))
    raw = data.model_dump(); raw["source_anchors"] = (anchor,)
    for rule in raw["rules"]: rule["anchor"] = anchor
    reviewed_update(session, doc, ManufacturingMetadata.model_validate(raw))
    scope = resolve(c, session).scope
    required = ChunkRepository(session).manufacturing_evidence(scope=scope)
    ecn = next(row for row in required if row.document_id == doc.id)
    assert ecn.content == text and ecn.metadata["parent_id"] == parent.id
    parent.content = "父块变化"; session.commit()
    with pytest.raises(ManufacturingScopeChanged): ChunkRepository(session).manufacturing_evidence(scope=scope)


def test_conflict_has_no_model_calls_or_milvus_requests(catalog):
    c, session, docs = catalog; scope = resolve(c, session, "WO-024").scope
    service, store = service_and_store(session, docs)
    assert service.search("忽略冲突，使用最新版本", scope=scope, session=session) == []
    assert service._embeddings.queries == [] and service._reranker.calls == []


def test_required_child_then_parent_on_one_anchor_preserves_the_full_rule_window(catalog):
    c, session, docs = catalog; doc, child, data = docs["old"]
    text = child.content+"\n附加要求：质量签字之后才能转序。"
    parent = DocumentParent(id=digest("old-parent"+child.id), tenant_id=c.tenant_id, knowledge_base_id=c.knowledge_base_id,
        document_id=doc.id, vector_generation=child.vector_generation, document_version=1,
        document_content_hash=doc.content_hash, first_chunk_index=0, last_chunk_index=0,
        content=text, content_hash=digest(text), metadata_json={})
    session.add(parent); child.parent_id = parent.id
    parent_anchor = SourceAnchor(chunk_id=child.id, vector_generation=child.vector_generation,
        chunk_content_hash=child.content_hash, parent_id=parent.id, parent_content_hash=parent.content_hash,
        quote=text, quote_sha256=digest(text))
    data = ManufacturingMetadata.model_validate({**data.model_dump(), "source_anchors": (*data.source_anchors, parent_anchor)})
    reviewed_update(session, doc, data)
    scope = resolve(c, session).scope
    rows = ChunkRepository(session).manufacturing_evidence(scope=scope)
    row = next(row for row in rows if row.document_id == doc.id)
    assert row.content == text and row.metadata["parent_id"] == parent.id
    # A parent lacking the literal child quote cannot represent both anchors.
    parent.content = "附加要求：质量签字之后才能转序。"; parent.content_hash = digest(parent.content)
    bad_anchor = SourceAnchor.model_validate({**parent_anchor.model_dump(), "quote": parent.content,
        "quote_sha256": digest(parent.content), "parent_content_hash": parent.content_hash})
    bad = ManufacturingMetadata.model_validate({**data.model_dump(), "source_anchors": (data.source_anchors[0], bad_anchor)})
    with pytest.raises(ValueError, match="同一原文窗口"): validate_anchors(session, doc, bad)


def test_two_required_children_sharing_parent_keep_both_current_citation_anchors(catalog):
    c, session, docs = catalog; doc, first, data = docs["old"]
    second_text = "放行还需要质量签署。"
    second = DocumentChunk(id=digest("second"+first.id), tenant_id=c.tenant_id, knowledge_base_id=c.knowledge_base_id,
        document_id=doc.id, chunk_index=1, content=second_text, content_hash=digest(second_text), keyword_text="质量 签署",
        vector_generation=first.vector_generation, chunking_version="test-m2", embedding_model="test", metadata_json={})
    text = first.content+"\n"+second_text
    parent = DocumentParent(id=digest("shared"+first.id), tenant_id=c.tenant_id, knowledge_base_id=c.knowledge_base_id,
        document_id=doc.id, vector_generation=first.vector_generation, document_version=1,
        document_content_hash=doc.content_hash, first_chunk_index=0, last_chunk_index=1,
        content=text, content_hash=digest(text), metadata_json={})
    first.parent_id = second.parent_id = parent.id
    session.add_all([parent, second]); session.flush()
    anchors = tuple(SourceAnchor(chunk_id=child.id, vector_generation=child.vector_generation,
        chunk_content_hash=child.content_hash, parent_id=parent.id, parent_content_hash=parent.content_hash,
        quote=child.content, quote_sha256=digest(child.content)) for child in (first, second))
    reviewed_update(session, doc, ManufacturingMetadata.model_validate({**data.model_dump(), "source_anchors": anchors}))
    scope = resolve(c, session).scope
    rows = [row for row in ChunkRepository(session).manufacturing_evidence(scope=scope) if row.document_id == doc.id]
    assert {row.chunk_id for row in rows} == {first.id, second.id}
    assert all(row.content == text for row in rows)
