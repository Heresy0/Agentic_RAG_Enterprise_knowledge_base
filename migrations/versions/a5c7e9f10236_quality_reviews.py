"""Human review history, export consent and regression version manifests.

Revision ID: a5c7e9f10236
Revises: f4b6d8e0a215
"""
from alembic import op
import sqlalchemy as sa

revision = "a5c7e9f10236"
down_revision = "f4b6d8e0a215"
branch_labels = depends_on = None


def upgrade():
    op.add_column("qa_feedback", sa.Column("allow_dataset_export", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_check_constraint("ck_qa_feedback_export_consent", "qa_feedback", "NOT allow_dataset_export OR share_with_admins")
    op.create_table("quality_cases",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("review_status", sa.String(24), nullable=False), sa.Column("sample_status", sa.String(16), nullable=False),
        sa.Column("root_cause", sa.String(32)), sa.Column("source_binding", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_quality_cases_tenant_id"),
        sa.UniqueConstraint("tenant_id", "run_id", name="uq_quality_cases_run"),
        sa.ForeignKeyConstraint(["tenant_id", "run_id"], ["qa_runs.tenant_id", "qa_runs.id"], ondelete="CASCADE"),
        sa.CheckConstraint("revision >= 1", name="ck_quality_cases_revision"),
        sa.CheckConstraint("review_status IN ('pending','confirmed','dismissed','needs_evidence')", name="ck_quality_cases_status"),
        sa.CheckConstraint("sample_status IN ('draft','approved')", name="ck_quality_cases_sample"),
        sa.CheckConstraint("sample_status != 'approved' OR review_status = 'confirmed'", name="ck_quality_cases_approval"))
    op.create_index("ix_quality_cases_queue", "quality_cases", ["tenant_id", "review_status", "updated_at", "id"])
    op.create_table("quality_case_revisions",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True), sa.Column("case_id", sa.Uuid(), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True), sa.Column("reviewer_user_id", sa.Uuid(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False), sa.Column("source_binding", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "case_id"], ["quality_cases.tenant_id", "quality_cases.id"], ondelete="CASCADE"),
        sa.CheckConstraint("revision >= 1", name="ck_quality_case_revisions_number"))
    op.create_table("quality_dataset_versions",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False), sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_quality_dataset_versions_created", "quality_dataset_versions", ["tenant_id", "created_at", "id"])


def downgrade():
    op.drop_table("quality_dataset_versions")
    op.drop_table("quality_case_revisions")
    op.drop_table("quality_cases")
    op.drop_constraint("ck_qa_feedback_export_consent", "qa_feedback", type_="check")
    op.drop_column("qa_feedback", "allow_dataset_export")
