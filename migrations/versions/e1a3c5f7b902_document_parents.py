"""Store scoped, versioned parent text without rebuilding child vectors."""
from alembic import op
import sqlalchemy as sa

revision = 'e1a3c5f7b902'
down_revision = 'd0f2a4c6e809'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('document_parents',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('knowledge_base_id', sa.Uuid(), nullable=False),
        sa.Column('document_id', sa.Uuid(), nullable=False),
        sa.Column('vector_generation', sa.String(32), nullable=False),
        sa.Column('document_version', sa.Integer(), nullable=False),
        sa.Column('document_content_hash', sa.String(64), nullable=False),
        sa.Column('first_chunk_index', sa.Integer(), nullable=False),
        sa.Column('last_chunk_index', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['tenant_id','knowledge_base_id','document_id'],
            ['documents.tenant_id','documents.knowledge_base_id','documents.id'],
            name='fk_document_parents_document', ondelete='CASCADE'),
        sa.CheckConstraint('first_chunk_index >= 0 AND last_chunk_index >= first_chunk_index', name='ck_document_parents_range'),
        sa.CheckConstraint('document_version >= 1', name='ck_document_parents_version'))
    op.create_index('ix_document_parents_scope', 'document_parents',
                    ['tenant_id','knowledge_base_id','document_id','vector_generation'])


def downgrade():
    op.drop_table('document_parents')
