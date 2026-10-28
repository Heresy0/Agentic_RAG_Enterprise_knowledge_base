"""Bounded private conversation state; existing conversations bootstrap lazily."""
from alembic import op
import sqlalchemy as sa

revision = 'f2b4d6e8a013'
down_revision = 'e1a3c5f7b902'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('conversations', sa.Column('memory_json', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('conversations', 'memory_json')
