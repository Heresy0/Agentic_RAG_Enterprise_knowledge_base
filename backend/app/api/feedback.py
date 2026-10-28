"""Owner feedback and consent-based, all-sources-authorized administrator listing."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit_service import AuditService
from ..db.dependencies import get_database_session
from ..db.quality_models import QARun, QARunScope, QAFeedback
from ..quality.service import FeedbackStore, ObservationNotFound
from ..security.authorization import AuthorizationDenied
from ..security.dependencies import get_current_principal
from ..security.principal import Principal

def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/qa", tags=["qa-feedback"], dependencies=[Depends(no_store)])
Reason = Literal["incorrect", "incomplete", "citation", "false_refusal", "outdated", "other"]


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Literal["up", "down"]
    reason: Reason | None = None
    comment: str | None = Field(default=None, max_length=2000)
    share_with_admins: bool = False
    allow_dataset_export: bool = False

    @field_validator("comment")
    @classmethod
    def clean_comment(cls, value):
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def valid_reason(self):
        if (self.rating == "down") != (self.reason is not None):
            raise ValueError("点踩时必须选择原因；点赞时不填写错误原因。")
        if self.allow_dataset_export and not self.share_with_admins:
            raise ValueError("允许回归样本导出前，必须先共享给管理员。")
        return self


def store(session: Session = Depends(get_database_session), principal: Principal = Depends(get_current_principal)):
    return FeedbackStore(session, principal)


def checked_run(store, run_id, **kwargs):
    try:
        return store.get_run(run_id, **kwargs)
    except ObservationNotFound:
        raise HTTPException(404, "未找到可访问的问答快照。") from None


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def feedback_data(row):
    if row is None:
        return None
    return dict(id=row.id, run_id=row.run_id, rating=row.rating, reason=row.reason, comment=row.comment,
                share_with_admins=row.share_with_admins, allow_dataset_export=row.allow_dataset_export,
                created_at=utc(row.created_at), updated_at=utc(row.updated_at))


@router.get("/runs/{run_id}")
def snapshot_detail(run_id: UUID, store: FeedbackStore = Depends(store)):
    run = checked_run(store, run_id)
    return dict(run_id=run.id, request_id=run.request_id, question=run.question,
                retrieval_question=run.retrieval_question, response=run.response_json,
                snapshot=run.snapshot_json, snapshot_status=run.snapshot_status, config=run.config_json,
                created_at=utc(run.created_at), feedback=feedback_data(store.feedback(run)))


@router.put("/runs/{run_id}/feedback")
def put_feedback(run_id: UUID, payload: FeedbackInput, store: FeedbackStore = Depends(store)):
    run = checked_run(store, run_id, owner=True, lock=True)
    try:
        row = store.put(run, payload)
        AuditService(store.session).add(tenant_id=run.tenant_id, actor_user_id=store.principal.user_id,
            action="qa.feedback_saved", resource_type="qa_run", resource_id=run.id, outcome="success",
            request_id=run.request_id)
        store.session.commit()
        return feedback_data(row)
    except Exception:
        store.session.rollback()
        raise


@router.delete("/runs/{run_id}/feedback", status_code=204)
def delete_feedback(run_id: UUID, store: FeedbackStore = Depends(store)):
    run = checked_run(store, run_id, owner=True, lock=True)
    row = store.feedback(run)
    try:
        if row is not None:
            store.session.delete(row)
            AuditService(store.session).add(tenant_id=run.tenant_id, actor_user_id=store.principal.user_id,
                action="qa.feedback_deleted", resource_type="qa_run", resource_id=run.id, outcome="success",
                request_id=run.request_id)
        store.session.commit()
    except Exception:
        store.session.rollback()
        raise
    return Response(status_code=204)


@router.get("/feedback")
def list_feedback(view: Literal["mine", "shared"] = "mine", knowledge_base_id: UUID | None = None,
                  rating: Literal["up", "down"] | None = None, reason: Reason | None = None,
                  before: datetime | None = None, before_id: UUID | None = None,
                  limit: int = Query(20, ge=1, le=100), store: FeedbackStore = Depends(store)):
    if (before is None) != (before_id is None) or before is not None and before.tzinfo is None:
        raise HTTPException(422, "游标必须同时包含带时区的 before 和 before_id。")
    if knowledge_base_id is not None:
        try:
            store.authorization.require_permission(principal=store.principal, knowledge_base_id=knowledge_base_id,
                                                    required_permission="admin" if view == "shared" else "viewer")
        except AuthorizationDenied:
            raise HTTPException(403, "没有权限查看该知识库的反馈。") from None
    predicates = store.scope_predicates(store.allowed_ids(admin=view == "shared"))
    predicates += [QAFeedback.tenant_id == store.principal.tenant_id,
                   QAFeedback.share_with_admins.is_(True) if view == "shared" else QAFeedback.user_id == store.principal.user_id]
    if knowledge_base_id is not None:
        predicates.append(QARun.id.in_(select(QARunScope.run_id).where(
            QARunScope.tenant_id == store.principal.tenant_id, QARunScope.knowledge_base_id == knowledge_base_id)))
    if rating:
        predicates.append(QAFeedback.rating == rating)
    if reason:
        predicates.append(QAFeedback.reason == reason)
    if before is not None:
        predicates.append((QAFeedback.updated_at < before) | ((QAFeedback.updated_at == before) & (QAFeedback.id < before_id)))
    rows = store.session.execute(select(QAFeedback, QARun).join(QARun,
        (QAFeedback.tenant_id == QARun.tenant_id) & (QAFeedback.run_id == QARun.id))
        .where(*predicates).order_by(QAFeedback.updated_at.desc(), QAFeedback.id.desc()).limit(limit)).all()
    items = [dict(**feedback_data(feedback), question=run.question, answer_preview=run.response_json.get("answer", "")[:300],
                  snapshot_status=run.snapshot_status) for feedback, run in rows if store.sources_available(run)]
    cursor = dict(before=utc(rows[-1][0].updated_at), before_id=rows[-1][0].id) if len(rows) == limit else None
    return dict(items=items, next_cursor=cursor)
