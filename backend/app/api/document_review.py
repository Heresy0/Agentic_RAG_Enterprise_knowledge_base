"""Inspect and publish an indexed source under the existing editor permission."""
import hashlib
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit_service import AuditService
from ..db.dependencies import get_database_session
from ..db.models import DocumentChunk, DocumentIndexingJob, KnowledgeDocument, Tenant
from ..documents.managed_document_files import ManagedDocumentFileService, ManagedDocumentPathError
from ..documents.publication import is_published, publication, reviewed_publication
from ..security.authorization import AuthorizationDenied, AuthorizationService
from ..security.dependencies import get_current_principal
from ..security.principal import Principal
from ..security.retrieval_scope import RetrievalScope

router = APIRouter(prefix="/api/knowledge-bases", tags=["document-review"])


class PublicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    state: Literal["published", "draft", "withdrawn"]
    review_note: str = Field(min_length=1, max_length=2000)
    acknowledge_ocr: bool = False

    @field_validator("review_note")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请填写复核或撤回说明。")
        return value.strip()


def authorized_document(session, principal, kb, identifier, *, permission="editor", lock=False):
    try:
        AuthorizationService(session).require_permission(principal=principal,
            knowledge_base_id=kb, required_permission=permission)
    except AuthorizationDenied as exc:
        raise HTTPException(403, str(exc)) from None
    statement = select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == principal.tenant_id,
        KnowledgeDocument.knowledge_base_id == kb, KnowledgeDocument.id == identifier,
        KnowledgeDocument.status != "deleted").execution_options(populate_existing=True)
    row = session.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise HTTPException(404, "文档不存在。")
    return row


def summary(doc):
    return dict(id=doc.id, file_name=doc.file_name, status=doc.status, version=doc.version,
        mime_type=doc.mime_type, content_hash=doc.content_hash, publication=publication(doc),
        quality=(doc.metadata_json or {}).get("quality", {}),
        chunk_count=(doc.metadata_json or {}).get("chunk_count", 0))


@router.get("/{knowledge_base_id}/documents/{document_id}/review")
def inspect_document(knowledge_base_id: UUID, document_id: UUID, response: Response,
        after_index: int = Query(-1, ge=-1), limit: int = Query(20, ge=1, le=50),
        expected_version: int | None = Query(None, ge=1),
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    response.headers["Cache-Control"] = "no-store"
    doc = authorized_document(session, principal, knowledge_base_id, document_id, lock=True)
    if expected_version is not None and expected_version != doc.version:
        raise HTTPException(409, "文档已更新，请重新预览。")
    rows = list(session.scalars(select(DocumentChunk).where(DocumentChunk.tenant_id == principal.tenant_id,
        DocumentChunk.knowledge_base_id == knowledge_base_id, DocumentChunk.document_id == doc.id,
        DocumentChunk.chunk_index > after_index).order_by(DocumentChunk.chunk_index).limit(limit + 1)))
    keys = ("section_path", "chunk_type", "parser", "page_start", "page_end", "quality", "cleaning", "cleaning_version")
    return dict(document=summary(doc), items=[dict(id=c.id, index=c.chunk_index, content=c.content,
        metadata={k:c.metadata_json.get(k) for k in keys}) for c in rows[:limit]],
        next_after=rows[limit-1].chunk_index if len(rows) > limit else None)


@router.put("/{knowledge_base_id}/documents/{document_id}/publication")
def update_publication(knowledge_base_id: UUID, document_id: UUID, payload: PublicationInput,
        request: Request, response: Response, principal: Principal = Depends(get_current_principal),
        session: Session = Depends(get_database_session)):
    response.headers["Cache-Control"] = "no-store"
    # Same lock order as indexing: a reviewer cannot publish a concurrent candidate.
    session.scalar(select(Tenant).where(Tenant.id == principal.tenant_id).with_for_update())
    doc = authorized_document(session, principal, knowledge_base_id, document_id, lock=True)
    previous = publication(doc)
    if doc.version != payload.expected_version or previous.get("revision", 0) != payload.expected_revision:
        raise HTTPException(409, "文档或复核状态已变化，请重新加载后处理。")
    if payload.state == "published":
        if doc.status != "ready" or not (doc.metadata_json or {}).get("chunk_count"):
            raise HTTPException(409, "仅可发布索引完成且有正文的文档。")
        if (doc.metadata_json or {}).get("quality", {}).get("review_required") and not payload.acknowledge_ocr:
            raise HTTPException(422, "资料含待复核OCR，请核对原件并明确确认识别限制。")
    doc.metadata_json = {**dict(doc.metadata_json or {}), "publication": reviewed_publication(doc,
        state=payload.state, actor_id=principal.user_id, note=payload.review_note,
        acknowledge_ocr=payload.acknowledge_ocr)}
    AuditService(session).add(tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
        knowledge_base_id=knowledge_base_id, action="document.publication_changed", resource_type="document",
        resource_id=doc.id, outcome="success", request_id=getattr(request.state, "request_id", None),
        details={"document_version": doc.version, "reason": payload.state})
    session.commit()
    return summary(doc)


@router.get("/{knowledge_base_id}/documents/{document_id}/source")
def document_source(knowledge_base_id: UUID, document_id: UUID, request: Request,
        version: int = Query(..., ge=1), principal: Principal = Depends(get_current_principal),
        session: Session = Depends(get_database_session)):
    doc = authorized_document(session, principal, knowledge_base_id, document_id, permission="viewer")
    if not is_published(doc) or doc.status != "ready":
        authorized_document(session, principal, knowledge_base_id, document_id, permission="editor")
    if doc.version != version:
        raise HTTPException(409, "原件版本已变化，请重新查看文档。")
    manager = ManagedDocumentFileService(storage_dir=request.app.state.settings.document_storage_dir)
    scope = RetrievalScope(principal.tenant_id, knowledge_base_id=knowledge_base_id)
    try:
        path = manager.resolve_target_uri(doc.storage_uri, scope=scope)
        if not path.is_file() and doc.status != "ready":
            job = session.scalar(select(DocumentIndexingJob).where(DocumentIndexingJob.tenant_id == principal.tenant_id,
                DocumentIndexingJob.document_id == doc.id, DocumentIndexingJob.candidate_content_hash == doc.content_hash,
                DocumentIndexingJob.target_version == doc.version).order_by(DocumentIndexingJob.created_at.desc()).limit(1))
            if job is None:
                raise FileNotFoundError()
            path = manager.resolve_staged_uri(job.staged_storage_uri, scope=scope)
        if path.stat().st_size > request.app.state.settings.max_upload_bytes:
            raise HTTPException(413, "原件超过预览大小限制。")
        content = path.read_bytes()
    except (OSError, ManagedDocumentPathError):
        raise HTTPException(404, "受管原件暂不可用。") from None
    if hashlib.sha256(content).hexdigest() != doc.content_hash:
        raise HTTPException(409, "原件正在更新或指纹不一致，请稍后重新加载。")
    mime = "application/pdf" if doc.mime_type == "application/pdf" else "application/octet-stream"
    return Response(content, media_type=mime, headers={"Cache-Control":"no-store",
        "X-Content-Type-Options":"nosniff", "Content-Disposition": "inline; filename*=UTF-8''" + quote(doc.file_name)})
