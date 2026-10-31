"""增加独立工单权限及会话和回答的业务范围绑定。"""
from alembic import op
import sqlalchemy as sa

revision = "d8f0b2c43569"
down_revision = "c7e9a1b32458"
branch_labels = depends_on = None


def bounds(prefix, names):
    return [sa.CheckConstraint(f"length({name}) BETWEEN 1 AND 64 AND trim({name}) = {name}",
                              name=f"ck_{prefix}_{name}") for name in names]


def upgrade():
    op.create_table("business_scope_grants",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True), sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("source_system_id", sa.String(64), primary_key=True),
        sa.Column("plant_code", sa.String(64), primary_key=True),
        sa.Column("permission", sa.String(8), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"],
                                name="fk_business_grants_user", ondelete="CASCADE"),
        sa.CheckConstraint("permission IN ('view','review')", name="ck_business_grants_permission"),
        *bounds("business_grants", ("source_system_id", "plant_code")))
    op.create_table("conversation_business_bindings",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True), sa.Column("conversation_id", sa.Uuid(), primary_key=True),
        sa.Column("source_system_id", sa.String(64), nullable=False),
        sa.Column("plant_code", sa.String(64), nullable=False),
        sa.Column("work_order_id", sa.String(64), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "conversation_id"], ["conversations.tenant_id", "conversations.id"],
                                name="fk_business_binding_conversation", ondelete="CASCADE"),
        sa.CheckConstraint("length(payload_sha256) = 64", name="ck_business_binding_hash"),
        *bounds("business_binding", ("source_system_id", "plant_code", "work_order_id", "source_revision")))
    op.create_table("qa_run_business_scopes",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True), sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("source_system_id", sa.String(64), primary_key=True),
        sa.Column("plant_code", sa.String(64), primary_key=True),
        sa.Column("work_order_id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "run_id"], ["qa_runs.tenant_id", "qa_runs.id"],
                                name="fk_business_scope_run", ondelete="CASCADE"),
        *bounds("business_scope", ("source_system_id", "plant_code", "work_order_id")))
    op.create_index("ix_qa_run_business_scope_access", "qa_run_business_scopes",
                    ["tenant_id", "source_system_id", "plant_code", "run_id"])


def downgrade():
    connection = op.get_bind()
    for table in ("qa_run_business_scopes", "conversation_business_bindings", "business_scope_grants"):
        if connection.scalar(sa.text(f'SELECT EXISTS (SELECT 1 FROM "{table}" LIMIT 1)')):
            raise RuntimeError("仍有业务授权或历史业务范围绑定，拒绝直接降级；须先完成导出和安全迁移。")
    op.drop_table("qa_run_business_scopes")
    op.drop_table("conversation_business_bindings")
    op.drop_table("business_scope_grants")
