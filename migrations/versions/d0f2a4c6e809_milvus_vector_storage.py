"""Export legacy vectors into a durable import outbox and remove SQL vector storage."""
from alembic import op
import sqlalchemy as sa

revision = "d0f2a4c6e809"
down_revision = "c9e1f3b5d708"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_chunks", sa.Column("vector_generation", sa.String(32), nullable=True))
    op.create_table("vector_sync_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(100)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("operation IN ('upsert','delete')", name="ck_vector_sync_jobs_operation"),
        sa.CheckConstraint("status IN ('queued','succeeded','failed')", name="ck_vector_sync_jobs_status"),
    )
    op.create_index("ix_vector_sync_jobs_claim", "vector_sync_jobs", ["status", "available_at", "created_at"])
    op.execute("UPDATE document_chunks SET vector_generation = md5(document_id::text || ':' || (SELECT version FROM documents WHERE id = document_id)::text)")
    # Embeddings are copied losslessly before dropping the original column.
    op.execute("""
        INSERT INTO vector_sync_jobs (id, tenant_id, knowledge_base_id, document_id, operation, payload)
        SELECT md5('milvus-import:' || c.document_id::text)::uuid, c.tenant_id, c.knowledge_base_id, c.document_id, 'upsert',
               json_build_object('records', json_agg(json_build_object(
                   'chunk_id', c.id, 'tenant_id', c.tenant_id::text, 'knowledge_base_id', c.knowledge_base_id::text,
                   'document_id', c.document_id::text, 'generation', c.vector_generation,
                   'embedding', c.embedding::text::json) ORDER BY c.chunk_index))
        FROM document_chunks c GROUP BY c.tenant_id, c.knowledge_base_id, c.document_id
    """)
    op.alter_column("document_chunks", "vector_generation", nullable=False)
    op.drop_index("ix_document_chunks_embedding_hnsw", table_name="document_chunks")
    op.drop_column("document_chunks", "embedding")
    # pg_search 0.25.11 declares a dependency on the vector extension.
    # Keep that dependency installed; the application has no SQL vector storage/search.


def downgrade():
    raise RuntimeError("Milvus迁移不自动恢复旧向量；请恢复迁移前备份。")
