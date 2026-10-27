"""Tenant-scoped, immutable-by-API answer snapshots and owner feedback."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class QARun(Base):
    __tablename__ = "qa_runs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(Uuid)
    turn_index: Mapped[int | None] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_question: Mapped[str] = mapped_column(Text, nullable=False)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    snapshot_status: Mapped[str] = mapped_column(String(24), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_qa_runs_tenant_id"),
        UniqueConstraint("tenant_id", "id", "user_id", name="uq_qa_runs_owner_id"),
        UniqueConstraint("tenant_id", "request_id", name="uq_qa_runs_request"),
        UniqueConstraint("conversation_id", "turn_index", name="uq_qa_runs_turn"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id", "conversation_id"], ["conversations.tenant_id", "conversations.id"], ondelete="CASCADE"),
        CheckConstraint("snapshot_status IN ('captured', 'not_observed', 'incomplete')", name="ck_qa_runs_snapshot"),
        Index("ix_qa_runs_owner_created", "tenant_id", "user_id", "created_at", "id"),
    )


class QARunScope(Base):
    __tablename__ = "qa_run_scopes"
    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "run_id"], ["qa_runs.tenant_id", "qa_runs.id"], ondelete="CASCADE"),
        # No KB cascade: deleting a KB must never make a historical run less restricted.
        Index("ix_qa_run_scope_kb", "tenant_id", "knowledge_base_id", "run_id"),
    )


class QAFeedback(Base):
    __tablename__ = "qa_feedback"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    rating: Mapped[str] = mapped_column(String(4), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    share_with_admins: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "run_id", "user_id"], ["qa_runs.tenant_id", "qa_runs.id", "qa_runs.user_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "run_id", "user_id", name="uq_qa_feedback_owner_run"),
        CheckConstraint("rating IN ('up', 'down')", name="ck_qa_feedback_rating"),
        CheckConstraint("(rating = 'up' AND reason IS NULL) OR (rating = 'down' AND reason IS NOT NULL AND reason IN ('incorrect', 'incomplete', 'citation', 'false_refusal', 'outdated', 'other'))", name="ck_qa_feedback_reason"),
        Index("ix_qa_feedback_tenant_updated", "tenant_id", "updated_at", "id"),
    )
