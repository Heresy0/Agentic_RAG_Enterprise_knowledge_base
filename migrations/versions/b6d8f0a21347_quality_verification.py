"""Budgeted evaluation jobs, result reviews and verified improvements.

Revision ID: b6d8f0a21347
Revises: a5c7e9f10236
"""
from alembic import op
import sqlalchemy as sa

revision = "b6d8f0a21347"
down_revision = "a5c7e9f10236"
branch_labels = depends_on = None


def upgrade():
    op.create_table("quality_evaluation_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("active_slot", sa.Integer()), sa.Column("fixed_dataset_id", sa.Uuid(), nullable=False),
        sa.Column("feedback_dataset_id", sa.Uuid(), nullable=False),
        *[sa.Column(name, sa.JSON(), nullable=False) for name in ("inputs_json", "budget_json", "runtime_json", "records_json", "summary_json")],
        sa.Column("lease_token", sa.String(32)), sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_type", sa.String(100)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "id", name="uq_quality_eval_tenant_id"),
        sa.UniqueConstraint("tenant_id", "active_slot", name="uq_quality_eval_active_tenant"),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','cancelled')", name="ck_quality_eval_status"),
        sa.CheckConstraint("(status IN ('queued','running') AND active_slot IS NOT NULL AND active_slot = 1) OR (status NOT IN ('queued','running') AND active_slot IS NULL)", name="ck_quality_eval_slot"))
    op.create_index("ix_quality_eval_queue", "quality_evaluation_jobs", ["status", "created_at", "id"])
    op.create_table("quality_evaluation_reviews",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True), sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("sample_id", sa.String(64), primary_key=True), sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("response_binding", sa.String(64), nullable=False), sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False), sa.Column("reviewer_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "job_id"], ["quality_evaluation_jobs.tenant_id", "quality_evaluation_jobs.id"], ondelete="CASCADE"),
        sa.CheckConstraint("verdict IN ('passed','failed','uncertain')", name="ck_quality_eval_review_verdict"),
        sa.CheckConstraint("revision >= 1", name="ck_quality_eval_review_revision"))
    op.create_table("quality_improvements",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False), sa.Column("regression_case_id", sa.Uuid(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False), sa.Column("change_ref", sa.String(128), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False), sa.Column("verification_json", sa.JSON()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_quality_improvement_tenant_id"),
        sa.ForeignKeyConstraint(["tenant_id", "case_id"], ["quality_cases.tenant_id", "quality_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "regression_case_id"], ["quality_cases.tenant_id", "quality_cases.id"], ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('open','verified','closed')", name="ck_quality_improvement_status"),
        sa.CheckConstraint("revision >= 1", name="ck_quality_improvement_revision"))
    op.create_table("quality_improvement_events",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True), sa.Column("improvement_id", sa.Uuid(), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True), sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "improvement_id"], ["quality_improvements.tenant_id", "quality_improvements.id"], ondelete="CASCADE"))


def downgrade():
    for table in ("quality_improvement_events", "quality_improvements", "quality_evaluation_reviews", "quality_evaluation_jobs"):
        op.drop_table(table)
