"""Durable vector cleanup/import. Source visibility is always decided by SQL."""
import logging
from datetime import datetime, timedelta, timezone
from time import time

from sqlalchemy import select

from ..db.models import DocumentChunk, VectorSyncJob, Tenant
from ..security.retrieval_scope import RetrievalScope
from ..knowledge.milvus_store import vector_record_id

logger = logging.getLogger(__name__)


def enqueue_vector_delete(session, *, scope, document_id, generation=None):
    session.add(VectorSyncJob(tenant_id=scope.tenant_id, knowledge_base_id=scope.knowledge_base_id,
                             document_id=document_id, operation="delete", payload={"generation": generation}))


class VectorSyncService:
    def __init__(self, session, store):
        self.session, self.store = session, store

    def run_once(self):
        now = datetime.now(timezone.utc)
        job = self.session.scalar(select(VectorSyncJob).where(
            VectorSyncJob.status == "queued", VectorSyncJob.available_at <= now,
        ).order_by(VectorSyncJob.created_at, VectorSyncJob.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            self.session.rollback()
            return False
        job.attempt_count += 1
        scope = RetrievalScope(job.tenant_id, job.knowledge_base_id)
        try:
            if job.operation == "upsert":
                self.session.scalar(select(Tenant).where(Tenant.id == job.tenant_id).with_for_update())
                records = job.payload["records"]
                # An imported generation retired before delivery must not be resurrected.
                generations = {row["generation"] for row in records}
                present = self.session.scalar(select(DocumentChunk.id).where(
                    DocumentChunk.tenant_id == job.tenant_id, DocumentChunk.knowledge_base_id == job.knowledge_base_id,
                    DocumentChunk.document_id == job.document_id, DocumentChunk.vector_generation.in_(generations),
                ).limit(1))
                if present:
                    if any(row["tenant_id"] != str(job.tenant_id) or row["knowledge_base_id"] != str(job.knowledge_base_id)
                           or row["document_id"] != str(job.document_id) for row in records):
                        raise ValueError("导入任务范围不匹配")
                    self.store.upsert([{**row, "id": vector_record_id(row["chunk_id"], row["generation"]),
                                        "created_at": row.get("created_at", int(time()))} for row in records])
            elif job.operation == "delete":
                self.store.delete(scope=scope, document_id=job.document_id, generation=job.payload.get("generation"))
            else:
                raise ValueError("不支持的向量同步操作")
            job.status, job.payload, job.last_error = "succeeded", {}, None
        except Exception as exc:
            job.last_error = type(exc).__name__
            job.status = "failed" if job.attempt_count >= 10 else "queued"
            job.available_at = now + timedelta(seconds=min(60, job.attempt_count * 5))
            logger.warning("向量同步重试：job=%s error_type=%s", job.id, type(exc).__name__)
        self.session.commit()
        return True

    def cleanup_orphans(self, *, retention_hours=24, limit=1000):
        # Bounded sweep complements the durable delete outbox after failed publication.
        protected = set(self.session.scalars(select(DocumentChunk.vector_generation).distinct()).all())
        self.session.rollback()
        rows = self.store.old_records(cutoff=time() - retention_hours * 3600, limit=limit, exclude_generations=protected)
        cleaned = 0
        seen = set()
        for row in rows:
            key = (row["tenant_id"], row["knowledge_base_id"], row["document_id"], row["generation"])
            if key in seen or row["generation"] in protected:
                continue
            seen.add(key)
            from uuid import UUID
            scope = RetrievalScope(UUID(row["tenant_id"]), UUID(row["knowledge_base_id"]))
            try:
                self.session.scalar(select(Tenant).where(Tenant.id == scope.tenant_id).with_for_update())
                present = self.session.scalar(select(DocumentChunk.id).where(
                    DocumentChunk.vector_generation == row["generation"]).limit(1))
                if present is None:
                    self.store.delete(scope=scope, document_id=UUID(row["document_id"]), generation=row["generation"])
                    cleaned += 1
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise
        return cleaned
