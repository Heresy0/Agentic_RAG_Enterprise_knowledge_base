from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.db.models import DocumentChunk, KnowledgeDocument, VectorSyncJob
from backend.app.indexing.vector_sync_service import VectorSyncService, enqueue_vector_delete
from backend.app.indexing.document_indexing_service import DocumentIndexingService
from backend.app.security.retrieval_scope import RetrievalScope
from test_document_indexing_service import session, scope_data, FakeEmbeddings, _settings


def indexed(session, scope_data, tmp_path):
    scope = RetrievalScope(scope_data.tenant_id, scope_data.technology_kb_id)
    path = tmp_path / "vector-lifecycle.txt"
    path.write_text("旧版本规范内容。", encoding="utf-8")
    service = DocumentIndexingService(session=session, settings=_settings(), embeddings=FakeEmbeddings())
    service.index_file(file_path=path, scope=scope, created_by_user_id=scope_data.user_id)
    doc = session.scalar(select(KnowledgeDocument))
    return scope, path, service, doc.id, doc.content_hash


def test_vector_failure_does_not_replace_published_document(session, scope_data, tmp_path, indexing_vector_store):
    scope, path, service, doc_id, old_hash = indexed(session, scope_data, tmp_path)
    old_ids = set(session.scalars(select(DocumentChunk.id)))
    indexing_vector_store.fail = True
    path.write_text("新版本规范内容。", encoding="utf-8")
    with pytest.raises(RuntimeError):
        service.index_file(file_path=path, scope=scope, created_by_user_id=scope_data.user_id)
    doc = session.get(KnowledgeDocument, doc_id)
    assert doc.status == "ready" and doc.content_hash == old_hash and doc.version == 1
    assert set(session.scalars(select(DocumentChunk.id))) == old_ids
    assert session.scalar(select(VectorSyncJob.id)) is None


def test_failed_publication_leaves_only_unpublished_orphan_and_gc_keeps_current(session, scope_data, tmp_path,
        indexing_vector_store, monkeypatch):
    scope, path, service, doc_id, old_hash = indexed(session, scope_data, tmp_path)
    current = set(indexing_vector_store.records)
    path.write_text("未能发布的新版本。", encoding="utf-8")
    def fail_commit():
        raise RuntimeError("simulated SQL publication failure")
    with monkeypatch.context() as context:
        context.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError):
            service.index_file(file_path=path, scope=scope, created_by_user_id=scope_data.user_id)
    assert session.get(KnowledgeDocument, doc_id).content_hash == old_hash
    assert set(indexing_vector_store.records) > current
    for row in indexing_vector_store.records.values():
        row["created_at"] = 1
    cleaned = VectorSyncService(session, indexing_vector_store).cleanup_orphans(limit=1)
    assert cleaned == 1
    assert set(indexing_vector_store.records) == current


def test_outbox_delete_survives_sql_document_deletion_and_retries(session, scope_data, tmp_path, indexing_vector_store):
    scope, path, service, doc_id, old_hash = indexed(session, scope_data, tmp_path)
    enqueue_vector_delete(session, scope=scope, document_id=doc_id)
    session.delete(session.get(KnowledgeDocument, doc_id))
    session.commit()
    sync = VectorSyncService(session, indexing_vector_store)
    indexing_vector_store.fail = True
    assert sync.run_once()
    job = session.scalar(select(VectorSyncJob))
    assert job.status == "queued" and job.attempt_count == 1
    assert indexing_vector_store.records
    indexing_vector_store.fail = False
    job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    assert sync.run_once()
    assert job.status == "succeeded" and job.payload == {}
    assert not indexing_vector_store.records


def test_migration_import_is_idempotent_and_clears_vector_payload(session, scope_data, tmp_path, indexing_vector_store):
    scope, path, service, doc_id, old_hash = indexed(session, scope_data, tmp_path)
    records = [{k: v for k, v in row.items() if k not in ("id", "created_at")}
               for row in indexing_vector_store.records.values()]
    job = VectorSyncJob(tenant_id=scope.tenant_id, knowledge_base_id=scope.knowledge_base_id,
                       document_id=doc_id, operation="upsert", payload={"records": records})
    session.add(job)
    session.commit()
    count = len(indexing_vector_store.records)
    sync = VectorSyncService(session, indexing_vector_store)
    assert sync.run_once() and job.status == "succeeded"
    assert job.payload == {} and len(indexing_vector_store.records) == count
    assert not sync.run_once()


def test_retired_import_generation_is_not_resurrected(session, scope_data, tmp_path, indexing_vector_store):
    scope, path, service, doc_id, old_hash = indexed(session, scope_data, tmp_path)
    records = list(indexing_vector_store.records.values())
    session.add(VectorSyncJob(tenant_id=scope.tenant_id, knowledge_base_id=scope.knowledge_base_id,
        document_id=doc_id, operation="upsert", payload={"records": records}))
    session.delete(session.get(KnowledgeDocument, doc_id))
    session.commit()
    indexing_vector_store.records = {}
    assert VectorSyncService(session, indexing_vector_store).run_once()
    assert not indexing_vector_store.records


def test_missing_remote_vectors_are_rebuilt_instead_of_idempotently_skipped(session, scope_data, tmp_path,
        indexing_vector_store):
    scope, path, service, doc_id, old_hash = indexed(session, scope_data, tmp_path)
    indexing_vector_store.records = {}
    assert service.index_file(file_path=path, scope=scope, created_by_user_id=scope_data.user_id) > 0
    assert indexing_vector_store.records
    assert session.get(KnowledgeDocument, doc_id).content_hash == old_hash
