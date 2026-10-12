"""Model-call ledger, deliberately independent of business audit transactions."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, Integer, JSON, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ModelUsageEvent(Base):
    __tablename__ = "model_usage_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_model_usage_tenant_id"),
        Index("ix_model_usage_tenant_created", "tenant_id", "created_at", "id"),
        Index("ix_model_usage_request", "tenant_id", "request_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(Uuid)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    currency: Mapped[str | None] = mapped_column(String(3))
    details: Mapped[dict] = mapped_column(JSON, nullable=False)


class ModelUsageScope(Base):
    __tablename__ = "model_usage_scopes"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "event_id"],
                             ["model_usage_events.tenant_id", "model_usage_events.id"]),
        Index("ix_model_usage_scope_kb", "tenant_id", "knowledge_base_id", "event_id"),
    )
    event_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    # Historical attribution survives deletion of a knowledge base; never broadens visibility.
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
