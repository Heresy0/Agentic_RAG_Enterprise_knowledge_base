"""Join deployed conversation-memory schema and quality flywheel migrations.

The nullable legacy column is retained without enabling the experimental memory
implementation. Neither branch's previously recorded revisions are rewritten.
"""

revision = 'c7e9a1b32458'
down_revision = ('b6d8f0a21347', 'f2b4d6e8a013')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
