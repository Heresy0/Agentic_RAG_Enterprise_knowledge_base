"""Private answer snapshots and owner feedback.

Revision ID: f4b6d8e0a215
Revises: e1a3c5f7b902
"""
from alembic import op
import sqlalchemy as sa

revision = "f4b6d8e0a215"
down_revision = "e1a3c5f7b902"
branch_labels = depends_on = None


def upgrade():
    op.create_table("qa_runs",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False), sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.Uuid()), sa.Column("turn_index", sa.Integer()),
        sa.Column("question", sa.Text(), nullable=False), sa.Column("retrieval_question", sa.Text(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False), sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_status", sa.String(24), nullable=False), sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_qa_runs_tenant_id"),
        sa.UniqueConstraint("tenant_id", "id", "user_id", name="uq_qa_runs_owner_id"),
        sa.UniqueConstraint("tenant_id", "request_id", name="uq_qa_runs_request"),
        sa.UniqueConstraint("conversation_id", "turn_index", name="uq_qa_runs_turn"),
        sa.ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "conversation_id"], ["conversations.tenant_id", "conversations.id"], ondelete="CASCADE"),
        sa.CheckConstraint("snapshot_status IN ('captured', 'not_observed', 'incomplete')", name="ck_qa_runs_snapshot"))
    op.create_index("ix_qa_runs_owner_created", "qa_runs", ["tenant_id", "user_id", "created_at", "id"])
    op.create_table("qa_run_scopes",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True), sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("knowledge_base_id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(["tenant_id", "run_id"], ["qa_runs.tenant_id", "qa_runs.id"], ondelete="CASCADE"))
    op.create_index("ix_qa_run_scope_kb", "qa_run_scopes", ["tenant_id", "knowledge_base_id", "run_id"])
    op.create_table("qa_feedback",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False), sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.String(4), nullable=False), sa.Column("reason", sa.String(32)), sa.Column("comment", sa.Text()),
        sa.Column("share_with_admins", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "run_id", "user_id"], ["qa_runs.tenant_id", "qa_runs.id", "qa_runs.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "run_id", "user_id", name="uq_qa_feedback_owner_run"),
        sa.CheckConstraint("rating IN ('up', 'down')", name="ck_qa_feedback_rating"),
        sa.CheckConstraint("(rating = 'up' AND reason IS NULL) OR (rating = 'down' AND reason IS NOT NULL AND reason IN ('incorrect', 'incomplete', 'citation', 'false_refusal', 'outdated', 'other'))", name="ck_qa_feedback_reason"))
    op.create_index("ix_qa_feedback_tenant_updated", "qa_feedback", ["tenant_id", "updated_at", "id"])


def downgrade():
    op.drop_table("qa_feedback")
    op.drop_table("qa_run_scopes")
    op.drop_table("qa_runs")
