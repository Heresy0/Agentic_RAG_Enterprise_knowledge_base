"""将制造会话绑定到明确工序及作业卡。"""
from alembic import op
import sqlalchemy as sa

revision = 'f2b8c0a961d3'
down_revision = 'e9a1c3d54670'
branch_labels = depends_on = None


def upgrade():
    op.add_column('conversation_business_bindings', sa.Column('operation_id', sa.String(64), nullable=True))
    op.add_column('conversation_business_bindings', sa.Column('job_card_id', sa.String(64), nullable=True))
    for name in ('operation_id', 'job_card_id'):
        op.create_check_constraint('ck_business_binding_' + name, 'conversation_business_bindings',
            f'length({name}) BETWEEN 1 AND 64 AND trim({name}) = {name}')
    op.create_check_constraint('ck_business_binding_execution_parent', 'conversation_business_bindings',
        'job_card_id IS NULL OR operation_id IS NOT NULL')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM conversation_business_bindings WHERE operation_id IS NOT NULL OR job_card_id IS NOT NULL)')):
        raise RuntimeError('存在已绑定工序或作业卡的会话，拒绝丢失业务边界。')
    for name in ('execution_parent', 'job_card_id', 'operation_id'):
        op.drop_constraint('ck_business_binding_' + name, 'conversation_business_bindings', type_='check')
    op.drop_column('conversation_business_bindings', 'job_card_id')
    op.drop_column('conversation_business_bindings', 'operation_id')
