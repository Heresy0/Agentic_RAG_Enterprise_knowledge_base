"""新增OpenMES步骤指导书审核修订，保留原执行来源。"""
from alembic import op
import sqlalchemy as sa

revision='e9b2d4f60713'
down_revision='d7a1c3e5f902'
branch_labels=depends_on=None


def upgrade():
    op.add_column('conversation_business_bindings',sa.Column('step_guidance_sha256',sa.String(64),nullable=True))
    op.create_check_constraint('ck_business_binding_guidance_hash','conversation_business_bindings','step_guidance_sha256 IS NULL OR length(step_guidance_sha256)=64')
    op.create_table('step_guidance_revisions',
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('tenant_id',sa.Uuid(),nullable=False),
        sa.Column('knowledge_base_id',sa.Uuid(),nullable=False),
        *(sa.Column(name,sa.String(64),nullable=False) for name in ('source_system_id','plant_code','work_order_id','batch_id','step_id')),
        sa.Column('revision',sa.Integer(),nullable=False),sa.Column('state',sa.String(12),nullable=False),
        sa.Column('payload_json',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.Column('actor_user_id',sa.Uuid(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['tenant_id','knowledge_base_id'],['knowledge_bases.tenant_id','knowledge_bases.id'],ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id','actor_user_id'],['users.tenant_id','users.id']),
        sa.UniqueConstraint('tenant_id','knowledge_base_id','source_system_id','plant_code','work_order_id','batch_id','step_id','revision',name='uq_step_guidance_revision'),
        sa.CheckConstraint('revision > 0',name='ck_step_guidance_revision'),
        sa.CheckConstraint("state IN ('draft','reviewed','withdrawn')",name='ck_step_guidance_state'),
        sa.CheckConstraint('length(payload_sha256)=64',name='ck_step_guidance_digest'),
        *(sa.CheckConstraint(f'length({name}) BETWEEN 1 AND 64 AND trim({name}) = {name}',name='ck_step_guidance_'+name)
          for name in ('source_system_id','plant_code','work_order_id','batch_id','step_id')))


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM step_guidance_revisions)')):
        raise RuntimeError('存在步骤指导书审核历史，拒绝降级丢失审核记录。')
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM conversation_business_bindings WHERE step_guidance_sha256 IS NOT NULL)')):
        raise RuntimeError('存在步骤映射绑定会话，拒绝降级丢失业务边界。')
    op.drop_table('step_guidance_revisions')
    op.drop_constraint('ck_business_binding_guidance_hash','conversation_business_bindings',type_='check')
    op.drop_column('conversation_business_bindings','step_guidance_sha256')
