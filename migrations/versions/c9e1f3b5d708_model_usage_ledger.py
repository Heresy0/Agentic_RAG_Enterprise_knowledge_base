"""Independent physical model-call ledger.

Revision ID: c9e1f3b5d708
Revises: b8d0f2a4c607
"""
from alembic import op
import sqlalchemy as sa

revision = "c9e1f3b5d708"
down_revision = "b8d0f2a4c607"
branch_labels = depends_on = None


def upgrade():
    op.create_table("model_usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False), sa.Column("actor_user_id", sa.Uuid()),
        sa.Column("stage", sa.String(32), nullable=False), sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()), sa.Column("total_tokens", sa.Integer()),
        sa.Column("estimated_cost", sa.Numeric(24, 10)), sa.Column("currency", sa.String(3)),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_model_usage_tenant_id"))
    op.create_index("ix_model_usage_tenant_created", "model_usage_events", ["tenant_id", "created_at", "id"])
    op.create_index("ix_model_usage_request", "model_usage_events", ["tenant_id", "request_id"])
    op.create_table("model_usage_scopes",
        sa.Column("event_id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(["tenant_id", "event_id"], ["model_usage_events.tenant_id", "model_usage_events.id"]))
    op.create_index("ix_model_usage_scope_kb", "model_usage_scopes", ["tenant_id", "knowledge_base_id", "event_id"])


def downgrade():
    op.drop_table("model_usage_scopes")
    op.drop_table("model_usage_events")
