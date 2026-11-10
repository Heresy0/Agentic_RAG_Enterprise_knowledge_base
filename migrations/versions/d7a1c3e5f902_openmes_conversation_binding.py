"""将制造会话绑定至OpenMES批次及步骤，保留ERP绑定。"""
from alembic import op
import sqlalchemy as sa

revision='d7a1c3e5f902'
down_revision='f2b8c0a961d3'
branch_labels=depends_on=None


def upgrade():
    for name in ('batch_id','step_id'):
        op.add_column('conversation_business_bindings',sa.Column(name,sa.String(64),nullable=True))
        op.create_check_constraint('ck_business_binding_'+name,'conversation_business_bindings',
            f'length({name}) BETWEEN 1 AND 64 AND trim({name}) = {name}')
    op.create_check_constraint('ck_business_binding_batch_parent','conversation_business_bindings','step_id IS NULL OR batch_id IS NOT NULL')
    op.create_check_constraint('ck_business_binding_execution_kind','conversation_business_bindings','batch_id IS NULL OR operation_id IS NULL')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM conversation_business_bindings WHERE batch_id IS NOT NULL OR step_id IS NOT NULL)')):
        raise RuntimeError('存在已绑定批次或步骤的会话，拒绝丢失业务边界。')
    for name in ('execution_kind','batch_parent','step_id','batch_id'):
        op.drop_constraint('ck_business_binding_'+name,'conversation_business_bindings',type_='check')
    op.drop_column('conversation_business_bindings','step_id')
    op.drop_column('conversation_business_bindings','batch_id')
