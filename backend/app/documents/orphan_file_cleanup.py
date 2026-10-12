"""只回收受管目录中已过保留期、无任务引用的临时文件。"""
import logging
import re
import time
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DocumentIndexingJob, KnowledgeDocument, Tenant
from ..security.retrieval_scope import RetrievalScope
from .managed_document_files import ManagedDocumentFileService, ManagedDocumentPathError

logger = logging.getLogger(__name__)
TEMP_FILE = re.compile(r"(?:\.deleting-[0-9a-f]{32}|\.backup-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.tmp\Z")
STAGED_FILE = re.compile(r"[0-9a-f]{32}\.(?:pdf|docx|txt|md)\Z")


def _ordinary(path: Path) -> bool:
    return not path.is_symlink() and not getattr(path, "is_junction", lambda: False)()


def cleanup_orphan_files(*, session: Session, storage_dir: str | Path,
                         retention_hours: float = 24, limit: int = 100) -> int:
    if not 0 < retention_hours or not 1 <= limit <= 1000:
        raise ValueError("保留期须为正数，清理上限须介于 1 和 1000")
    raw_root = Path(storage_dir).expanduser()
    if not _ordinary(raw_root):
        return 0
    root = raw_root.resolve()
    if not root.is_dir():
        return 0
    service = ManagedDocumentFileService(storage_dir=root)
    cutoff, cleaned = time.time() - retention_hours * 3600, 0
    for tenant_dir in root.iterdir():
        if not _ordinary(tenant_dir) or not tenant_dir.is_dir():
            continue
        try:
            tenant_id = UUID(tenant_dir.name)
        except ValueError:
            continue
        if tenant_dir.name != str(tenant_id):
            continue
        for scope_dir in tenant_dir.iterdir():
            if not _ordinary(scope_dir) or not scope_dir.is_dir():
                continue
            try:
                kb_id = UUID(scope_dir.name)
            except ValueError:
                continue
            if scope_dir.name != str(kb_id):
                continue
            if scope_dir.resolve().parent.parent != root:
                continue
            scope = RetrievalScope(tenant_id, kb_id)
            try:
                # 与上传的任务提交串行；刚写入的候选文件由保留期保护。
                tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
                if tenant is None:
                    session.rollback()
                    continue
                jobs = session.scalars(select(DocumentIndexingJob).where(
                    DocumentIndexingJob.tenant_id == tenant_id,
                    DocumentIndexingJob.knowledge_base_id == kb_id,
                )).all()
                protected = {service.resolve_staged_uri(job.staged_storage_uri, scope=scope) for job in jobs}
                documents = session.scalars(select(KnowledgeDocument).where(
                    KnowledgeDocument.tenant_id == tenant_id, KnowledgeDocument.knowledge_base_id == kb_id,
                )).all()
                # 正式文件缺失时备份可能是唯一副本，保留以供人工恢复。
                keep_backups = any(job.status in {"queued", "running"} for job in jobs) or any(
                    not service.resolve_target_uri(doc.storage_uri, scope=scope).is_file() for doc in documents
                )
                candidates = [p for p in scope_dir.iterdir() if TEMP_FILE.fullmatch(p.name) and not keep_backups]
                staging = scope_dir / ".staging"
                if _ordinary(staging) and staging.is_dir():
                    candidates.extend(p for p in staging.iterdir() if STAGED_FILE.fullmatch(p.name))
                for path in candidates:
                    if cleaned >= limit:
                        break
                    if not _ordinary(path) or not path.is_file() or path.resolve() in protected:
                        continue
                    if path.resolve().parent not in {scope_dir.resolve(), staging.resolve()}:
                        continue
                    if path.stat().st_mtime > cutoff:
                        continue
                    try:
                        path.unlink()
                        cleaned += 1
                    except OSError:
                        logger.warning("孤儿临时文件清理失败：tenant=%s kb=%s", tenant_id, kb_id)
                session.commit()
            except (ManagedDocumentPathError, OSError):
                # 不认识的引用或路径宁可保留，不猜测其归属。
                session.rollback()
                logger.warning("跳过无法安全确认的临时文件目录：tenant=%s kb=%s", tenant_id, kb_id)
            except Exception:
                session.rollback()
                raise
            if cleaned >= limit:
                return cleaned
    return cleaned
