"""Review revisions and reproducible, permission-checked regression manifests."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class QualityCase(Base):
    __tablename__ = "quality_cases"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    sample_status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    root_cause: Mapped[str | None] = mapped_column(String(32))
    source_binding: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_quality_cases_tenant_id"),
        UniqueConstraint("tenant_id", "run_id", name="uq_quality_cases_run"),
        ForeignKeyConstraint(["tenant_id", "run_id"], ["qa_runs.tenant_id", "qa_runs.id"], ondelete="CASCADE"),
        CheckConstraint("revision >= 1", name="ck_quality_cases_revision"),
        CheckConstraint("review_status IN ('pending','confirmed','dismissed','needs_evidence')", name="ck_quality_cases_status"),
        CheckConstraint("sample_status IN ('draft','approved')", name="ck_quality_cases_sample"),
        CheckConstraint("sample_status != 'approved' OR review_status = 'confirmed'", name="ck_quality_cases_approval"),
        Index("ix_quality_cases_queue", "tenant_id", "review_status", "updated_at", "id"),
    )


class QualityCaseRevision(Base):
    __tablename__ = "quality_case_revisions"
    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    case_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    reviewer_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_binding: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "case_id"], ["quality_cases.tenant_id", "quality_cases.id"], ondelete="CASCADE"),
        CheckConstraint("revision >= 1", name="ck_quality_case_revisions_number"),
    )


class QualityDatasetVersion(Base):
    __tablename__ = "quality_dataset_versions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (Index("ix_quality_dataset_versions_created", "tenant_id", "created_at", "id"),)
