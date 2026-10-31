"""持久记录制造会话失效，防止重新授权后复用旧工单上下文。"""
from alembic import op
import sqlalchemy as sa

revision = "e9a1c3d54670"
down_revision = "d8f0b2c43569"
branch_labels = depends_on = None


def upgrade():
    op.add_column("conversation_business_bindings", sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM conversation_business_bindings WHERE invalidated_at IS NOT NULL)")):
        raise RuntimeError("存在已失效的制造会话，拒绝移除防复用标记。")
    op.drop_column("conversation_business_bindings", "invalidated_at")
