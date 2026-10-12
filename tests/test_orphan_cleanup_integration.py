"""验证 PostgreSQL 行锁确实阻止清理与进行中的文档删除冲突。"""
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event
from time import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.app.db.models import KnowledgeBase, KnowledgeDocument, Tenant
from backend.app.documents.document_deletion_service import DocumentDeletionService
from backend.app.documents.orphan_file_cleanup import cleanup_orphan_files
from backend.app.security.retrieval_scope import RetrievalScope


@pytest.mark.integration
def test_sweep_waits_for_in_progress_deletion(tmp_path, monkeypatch):
    if os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1":
        pytest.skip("未启用 PostgreSQL 集成测试")
    url = os.environ["DATABASE_URL"]
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("只允许在名称以 _test 结尾的专用数据库执行")
    engine = create_engine(url)
    tenant_id, kb_id, doc_id = uuid4(), uuid4(), uuid4()
    scope = RetrievalScope(tenant_id, kb_id)
    storage = tmp_path / "documents"
    source = storage / str(tenant_id) / str(kb_id) / "old.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"only original")
    os.utime(source, (time() - 90000, time() - 90000))
    with Session(engine) as session:
        session.add(Tenant(id=tenant_id, name="回收并发测试"))
        session.flush()
        session.add(KnowledgeBase(id=kb_id, tenant_id=tenant_id, name="测试库", visibility="restricted"))
        session.flush()
        session.add(KnowledgeDocument(id=doc_id, tenant_id=tenant_id, knowledge_base_id=kb_id,
            source_id=uuid4().hex, file_name=source.name, storage_uri=source.as_uri(), mime_type="text/plain",
            content_hash="a" * 64, status="ready"))
        session.commit()
    renamed, finish, sweeping = Event(), Event(), Event()
    original_replace = Path.replace
    def pause_deletion(path, target):
        result = original_replace(path, target)
        if path == source:
            renamed.set()
            if not finish.wait(5):
                raise RuntimeError("测试未释放删除事务")
        return result
    monkeypatch.setattr(Path, "replace", pause_deletion)
    def delete():
        with Session(engine) as session:
            DocumentDeletionService(session=session, storage_dir=storage).delete(scope=scope, document_id=doc_id)
    def sweep():
        with Session(engine) as session:
            sweeping.set()
            return cleanup_orphan_files(session=session, storage_dir=storage)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            deletion = pool.submit(delete)
            assert renamed.wait(3)
            cleanup = pool.submit(sweep)
            try:
                assert sweeping.wait(1)
                with pytest.raises(TimeoutError):
                    cleanup.result(timeout=.2)
                assert len(list(source.parent.glob(".deleting-*.tmp"))) == 1
            finally:
                finish.set()
            deletion.result(timeout=5)
            assert cleanup.result(timeout=5) == 0
        with Session(engine) as session:
            assert session.get(KnowledgeDocument, doc_id) is None
    finally:
        finish.set()
        with Session(engine) as session:
            tenant = session.get(Tenant, tenant_id)
            if tenant:
                session.delete(tenant)
                session.commit()
        engine.dispose()
