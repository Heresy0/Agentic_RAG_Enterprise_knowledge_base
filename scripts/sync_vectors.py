"""Deliver Milvus outbox jobs without calling an embedding model."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, update
from backend.app.config import Settings
from backend.app.db.models import DocumentChunk, KnowledgeDocument, VectorSyncJob
from backend.app.db.session import create_database_engine, create_session_factory
from backend.app.indexing.vector_sync_service import VectorSyncService
from backend.app.knowledge.milvus_store import MilvusVectorStore


def audit(session, store):
    failures, chunks, documents = [], 0, 0
    groups = session.execute(select(DocumentChunk.tenant_id, DocumentChunk.knowledge_base_id,
        DocumentChunk.document_id, DocumentChunk.vector_generation, func.count()).group_by(
        DocumentChunk.tenant_id, DocumentChunk.knowledge_base_id, DocumentChunk.document_id,
        DocumentChunk.vector_generation)).all()
    for tenant, kb, document_id, generation, count in groups:
        expression = ' and '.join(f'{key} == {json.dumps(value)}' for key, value in {
            "tenant_id": str(tenant), "knowledge_base_id": str(kb),
            "document_id": str(document_id), "generation": generation,
        }.items())
        actual = store.count(expression)
        if actual != count:
            failures.append({"document_id": str(document_id), "expected": count, "actual": actual})
        chunks += count
        documents += 1
    session.rollback()
    return {"documents": documents, "chunks": chunks, "mismatches": failures}


def main():
    parser = argparse.ArgumentParser(description="重试 Milvus 导入/清理任务并检查当前版本向量数量。")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--require-imports", action="store_true", help="启动前只把未完成导入视为失败；删除由 Worker 重试。")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit 必须为正整数")
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    store = MilvusVectorStore(settings)
    try:
        store.ensure_collection()
        with create_session_factory(engine)() as session:
            if args.retry_failed:
                session.execute(update(VectorSyncJob).where(VectorSyncJob.status == "failed").values(
                    status="queued", attempt_count=0, last_error=None, available_at=datetime.now(timezone.utc)))
                session.commit()
            service = VectorSyncService(session, store)
            processed = 0
            for _ in range(args.limit):
                if not service.run_once():
                    break
                processed += 1
            pending_query = select(func.count()).select_from(VectorSyncJob).where(VectorSyncJob.status != "succeeded")
            if args.require_imports:
                pending_query = pending_query.where(VectorSyncJob.operation == "upsert")
            pending = session.scalar(pending_query)
            result = {"processed": processed, "pending": pending}
            if args.audit:
                result["audit"] = audit(session, store)
            print(json.dumps(result, ensure_ascii=False))
            if pending or result.get("audit", {}).get("mismatches"):
                raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
