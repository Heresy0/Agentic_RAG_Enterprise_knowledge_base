"""Business grants and immutable access bindings, separate from document grants."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from . import models, conversation_models, quality_models  # noqa: F401 -- register composite FK targets


def bounded_columns(prefix, names):
    return tuple(CheckConstraint(f"length({name}) BETWEEN 1 AND 64 AND trim({name}) = {name}",
                                 name=f"ck_{prefix}_{name}") for name in names)


class BusinessScopeGrant(Base):
    __tablename__ = "business_scope_grants"
    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    source_system_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plant_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    permission: Mapped[str] = mapped_column(String(8), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"],
                             name="fk_business_grants_user", ondelete="CASCADE"),
        CheckConstraint("permission IN ('view','review')", name="ck_business_grants_permission"),
        *bounded_columns("business_grants", ("source_system_id", "plant_code")),
    )


class ConversationBusinessBinding(Base):
    __tablename__ = "conversation_business_bindings"
    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    source_system_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plant_code: Mapped[str] = mapped_column(String(64), nullable=False)
    work_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "conversation_id"], ["conversations.tenant_id", "conversations.id"],
                             name="fk_business_binding_conversation", ondelete="CASCADE"),
        CheckConstraint("length(payload_sha256) = 64", name="ck_business_binding_hash"),
        *bounded_columns("business_binding", ("source_system_id", "plant_code", "work_order_id", "source_revision")),
    )


class QARunBusinessScope(Base):
    __tablename__ = "qa_run_business_scopes"
    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    source_system_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plant_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    work_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "run_id"], ["qa_runs.tenant_id", "qa_runs.id"],
                             name="fk_business_scope_run", ondelete="CASCADE"),
        *bounded_columns("business_scope", ("source_system_id", "plant_code", "work_order_id")),
        Index("ix_qa_run_business_scope_access", "tenant_id", "source_system_id", "plant_code", "run_id"),
        # No FK to grants: revocation must never erase a historical restriction.
    )
