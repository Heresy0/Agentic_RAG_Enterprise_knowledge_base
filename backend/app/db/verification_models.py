"""Budgeted evaluation jobs, human answer reviews and improvement decisions."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class QualityEvaluationJob(Base):
    __tablename__ = "quality_evaluation_jobs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    active_slot: Mapped[int | None] = mapped_column(Integer, default=1)
    fixed_dataset_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    feedback_dataset_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    inputs_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    budget_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    runtime_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    records_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    lease_token: Mapped[str | None] = mapped_column(String(32))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_type: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_quality_eval_tenant_id"),
        UniqueConstraint("tenant_id", "active_slot", name="uq_quality_eval_active_tenant"),
        CheckConstraint("status IN ('queued','running','succeeded','failed','cancelled')", name="ck_quality_eval_status"),
        CheckConstraint("(status IN ('queued','running') AND active_slot IS NOT NULL AND active_slot = 1) OR (status NOT IN ('queued','running') AND active_slot IS NULL)", name="ck_quality_eval_slot"),
        Index("ix_quality_eval_queue", "status", "created_at", "id"),
    )


class QualityEvaluationReview(Base):
    __tablename__ = "quality_evaluation_reviews"
    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sample_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    response_binding: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "job_id"], ["quality_evaluation_jobs.tenant_id", "quality_evaluation_jobs.id"], ondelete="CASCADE"),
        CheckConstraint("verdict IN ('passed','failed','uncertain')", name="ck_quality_eval_review_verdict"),
        CheckConstraint("revision >= 1", name="ck_quality_eval_review_revision"),
    )


class QualityImprovement(Base):
    __tablename__ = "quality_improvements"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    regression_case_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    owner_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    change_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    verification_json: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_quality_improvement_tenant_id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["quality_cases.tenant_id", "quality_cases.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id", "regression_case_id"], ["quality_cases.tenant_id", "quality_cases.id"], ondelete="CASCADE"),
        CheckConstraint("status IN ('open','verified','closed')", name="ck_quality_improvement_status"),
        CheckConstraint("revision >= 1", name="ck_quality_improvement_revision"),
    )


class QualityImprovementEvent(Base):
    __tablename__ = "quality_improvement_events"
    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    improvement_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "improvement_id"],
        ["quality_improvements.tenant_id", "quality_improvements.id"], ondelete="CASCADE"),)
