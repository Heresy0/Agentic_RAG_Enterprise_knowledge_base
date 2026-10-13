import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from types import SimpleNamespace
from backend.app.knowledge.milvus_store import MilvusVectorStore, make_vector_records
from sqlalchemy.orm import Session

from backend.app.config import Settings, required_env
from backend.app.db.models import (
    EMBEDDING_DIMENSION,
    DocumentChunk,
    KnowledgeBase,
    KnowledgeDocument,
    Tenant,
    User,
)
from backend.app.security.retrieval_scope import (
    RetrievalScope,
)
from backend.app.knowledge.chunk_repository import (
    ChunkRepository,
)
from backend.app.indexing.document_indexing_service import (
    DocumentIndexingService,
)
from langchain_core.embeddings import Embeddings


def _identifier() -> str:
    return uuid4().hex + uuid4().hex


def _embedding(
    first: float,
    second: float,
) -> list[float]:
    return (
        [first, second]
        + [0.0] * (EMBEDDING_DIMENSION - 2)
    )


class IntegrationEmbeddings(Embeddings):
    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        return [
            _embedding(1.0, 0.0)
            for _text in texts
        ]

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        del text
        return _embedding(1.0, 0.0)


def _settings() -> Settings:
    settings = object.__new__(Settings)
    settings.embedding_model = "text-embedding-v4"
    return settings


@pytest.fixture
def postgres_session() -> Iterator[Session]:
    if (
        os.getenv("RUN_POSTGRES_INTEGRATION_TESTS")
        != "1"
    ):
        pytest.skip(
            "未启用PostgreSQL集成测试"
        )

    url = required_env("DATABASE_URL")
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("集成测试只允许名称以 _test 结尾的专用数据库")
    engine = create_engine(
        url,
        pool_pre_ping=True,
    )
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        session.close()

        if transaction.is_active:
            transaction.rollback()

        connection.close()
        engine.dispose()


@pytest.fixture
def vector_store():
    store = MilvusVectorStore(SimpleNamespace(milvus_uri=os.getenv("MILVUS_URI", "http://127.0.0.1:19531"),
        milvus_collection="enterprise_milvus_test_" + uuid4().hex, milvus_timeout_seconds=20))
    try:
        store.ensure_collection()
        yield store
    finally:
        assert store.collection.startswith("enterprise_milvus_test_")
        store._call("collections/drop")


@pytest.mark.integration
def test_real_Milvus_search_is_scoped_and_ordered(
    postgres_session: Session, vector_store,
) -> None:
    tenant_a = Tenant(
        id=uuid4(),
        name="Milvus测试企业A",
    )
    tenant_b = Tenant(
        id=uuid4(),
        name="Milvus测试企业B",
    )
    postgres_session.add_all([
        tenant_a,
        tenant_b,
    ])
    postgres_session.flush()

    knowledge_base_a = KnowledgeBase(
        id=uuid4(),
        tenant_id=tenant_a.id,
        name="测试知识库A",
        visibility="restricted",
    )
    knowledge_base_b = KnowledgeBase(
        id=uuid4(),
        tenant_id=tenant_b.id,
        name="测试知识库B",
        visibility="restricted",
    )
    postgres_session.add_all([
        knowledge_base_a,
        knowledge_base_b,
    ])
    postgres_session.flush()

    document_a = KnowledgeDocument(
        id=uuid4(),
        tenant_id=tenant_a.id,
        knowledge_base_id=knowledge_base_a.id,
        created_by_user_id=None,
        source_id=_identifier(),
        file_name="企业A文档.txt",
        storage_uri="test://tenant-a/document",
        mime_type="text/plain",
        content_hash=_identifier(),
        status="ready",
        version=1,
        metadata_json={},
    )
    document_b = KnowledgeDocument(
        id=uuid4(),
        tenant_id=tenant_b.id,
        knowledge_base_id=knowledge_base_b.id,
        created_by_user_id=None,
        source_id=_identifier(),
        file_name="企业B文档.txt",
        storage_uri="test://tenant-b/document",
        mime_type="text/plain",
        content_hash=_identifier(),
        status="ready",
        version=1,
        metadata_json={},
    )
    postgres_session.add_all([
        document_a,
        document_b,
    ])
    postgres_session.flush()

    near_chunk_id = _identifier()
    far_chunk_id = _identifier()
    other_tenant_chunk_id = _identifier()

    postgres_session.add_all([
        DocumentChunk(
            id=near_chunk_id,
            tenant_id=tenant_a.id,
            knowledge_base_id=knowledge_base_a.id,
            document_id=document_a.id,
            chunk_index=0,
            content="企业A最相关内容",
            content_hash=_identifier(),
            chunking_version="structured-v1",
            embedding_model="text-embedding-v4",
            vector_generation="a" * 32,
            metadata_json={},
        ),
        DocumentChunk(
            id=far_chunk_id,
            tenant_id=tenant_a.id,
            knowledge_base_id=knowledge_base_a.id,
            document_id=document_a.id,
            chunk_index=1,
            content="企业A较不相关内容",
            content_hash=_identifier(),
            chunking_version="structured-v1",
            embedding_model="text-embedding-v4",
            vector_generation="a" * 32,
            metadata_json={},
        ),
        DocumentChunk(
            id=other_tenant_chunk_id,
            tenant_id=tenant_b.id,
            knowledge_base_id=knowledge_base_b.id,
            document_id=document_b.id,
            chunk_index=0,
            content="企业B完全匹配但不可见",
            content_hash=_identifier(),
            chunking_version="structured-v1",
            embedding_model="text-embedding-v4",
            vector_generation="a" * 32,
            metadata_json={},
        ),
    ])
    postgres_session.flush()

    all_chunks = list(postgres_session.scalars(select(DocumentChunk)))
    for doc_id in {c.document_id for c in all_chunks}:
        doc_chunks = [c for c in all_chunks if c.document_id == doc_id]
        vectors = [_embedding(0, 1) if c.id == far_chunk_id else _embedding(1, 0) for c in doc_chunks]
        vector_store.upsert(make_vector_records(doc_chunks, vectors))

    repository = ChunkRepository(postgres_session, vector_store)
    scope = RetrievalScope(
        tenant_id=tenant_a.id,
        knowledge_base_id=knowledge_base_a.id,
    )

    hits = repository.search(
        scope=scope,
        query_embedding=_embedding(1.0, 0.0),
        limit=10,
    )

    assert [
        hit.chunk_id
        for hit in hits
    ] == [
        near_chunk_id,
        far_chunk_id,
    ]

    assert all(
        hit.tenant_id == tenant_a.id
        for hit in hits
    )
    assert all(
        hit.knowledge_base_id
        == knowledge_base_a.id
        for hit in hits
    )
    assert other_tenant_chunk_id not in {
        hit.chunk_id
        for hit in hits
    }

    assert hits[0].distance == pytest.approx(
        0.0,
        abs=1e-6,
    )
    assert hits[1].distance == pytest.approx(
        1.0,
        abs=1e-6,
    )

@pytest.mark.integration
def test_index_file_and_search_real_Milvus(
    postgres_session: Session, vector_store,
    tmp_path,
) -> None:
    tenant = Tenant(
        id=uuid4(),
        name="Milvus写入测试企业",
    )
    postgres_session.add(tenant)
    postgres_session.flush()

    user = User(
        id=uuid4(),
        tenant_id=tenant.id,
        external_subject="Milvus-test-user",
        name="测试用户",
    )
    technology_kb = KnowledgeBase(
        id=uuid4(),
        tenant_id=tenant.id,
        name="Milvus技术知识库",
        visibility="restricted",
    )
    hr_kb = KnowledgeBase(
        id=uuid4(),
        tenant_id=tenant.id,
        name="Milvus人力知识库",
        visibility="restricted",
    )
    postgres_session.add_all([
        user,
        technology_kb,
        hr_kb,
    ])
    postgres_session.commit()

    file_path = tmp_path / "真实写入测试.txt"
    file_path.write_text(
        "这是一段写入真实Milvus的技术资料。",
        encoding="utf-8",
    )

    technology_scope = RetrievalScope(
        tenant_id=tenant.id,
        knowledge_base_id=technology_kb.id,
    )
    hr_scope = RetrievalScope(
        tenant_id=tenant.id,
        knowledge_base_id=hr_kb.id,
    )

    indexing_service = DocumentIndexingService(
        session=postgres_session,
        settings=_settings(),
        embeddings=IntegrationEmbeddings(),
        vector_store=vector_store,
    )

    written_count = indexing_service.index_file(
        file_path=file_path,
        scope=technology_scope,
        created_by_user_id=user.id,
    )

    assert written_count >= 1

    repository = ChunkRepository(postgres_session, vector_store)

    technology_hits = repository.search(
        scope=technology_scope,
        query_embedding=_embedding(1.0, 0.0),
        limit=10,
    )
    hr_hits = repository.search(
        scope=hr_scope,
        query_embedding=_embedding(1.0, 0.0),
        limit=10,
    )

    assert len(technology_hits) == written_count
    assert hr_hits == []

    assert all(
        hit.tenant_id == tenant.id
        for hit in technology_hits
    )
    assert all(
        hit.knowledge_base_id == technology_kb.id
        for hit in technology_hits
    )
    assert all(
        hit.document_name == "真实写入测试.txt"
        for hit in technology_hits
    )


@pytest.mark.integration
def test_legacy_migration_preserves_vectors_and_bm25(postgres_session, vector_store, tmp_path):
    import importlib
    import json
    from sqlalchemy import text
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    from backend.app.db.models import VectorSyncJob
    from backend.app.indexing.vector_sync_service import VectorSyncService
    tenant = Tenant(id=uuid4(), name="向量迁移测试")
    postgres_session.add(tenant)
    postgres_session.flush()
    kb = KnowledgeBase(id=uuid4(), tenant_id=tenant.id, name="迁移测试库")
    postgres_session.add(kb)
    postgres_session.flush()
    scope = RetrievalScope(tenant.id, kb.id)
    document = KnowledgeDocument(id=uuid4(), tenant_id=tenant.id, knowledge_base_id=kb.id,
        source_id=_identifier(), file_name="历史文档.md", storage_uri="test://migration", mime_type="text/markdown",
        content_hash=_identifier(), status="ready", version=3, metadata_json={"chunk_count": 1})
    postgres_session.add(document)
    postgres_session.flush()
    chunk = DocumentChunk(id=_identifier(), tenant_id=tenant.id, knowledge_base_id=kb.id, document_id=document.id,
        chunk_index=0, content="历史原文保持不变", keyword_text="历史 原文", content_hash=_identifier(),
        embedding_model="test", chunking_version="test", vector_generation=uuid4().hex)
    postgres_session.add(chunk)
    postgres_session.flush()
    chunk_id = chunk.id
    embedding = _embedding(0.25, 0.75)
    connection = postgres_session.connection()
    # Simulate the exact predecessor schema within the disposable test transaction.
    connection.execute(text("DROP TABLE vector_sync_jobs"))
    connection.execute(text("ALTER TABLE document_chunks DROP COLUMN vector_generation"))
    connection.execute(text("ALTER TABLE document_chunks ADD COLUMN embedding vector(1024)"))
    connection.execute(text("UPDATE document_chunks SET embedding = CAST(:embedding AS vector)"),
                       {"embedding": json.dumps(embedding)})
    connection.execute(text("ALTER TABLE document_chunks ALTER COLUMN embedding SET NOT NULL"))
    connection.execute(text("CREATE INDEX ix_document_chunks_embedding_hnsw ON document_chunks USING hnsw (embedding vector_cosine_ops)"))
    migration = importlib.import_module("migrations.versions.d0f2a4c6e809_milvus_vector_storage")
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    postgres_session.expire_all()
    job = postgres_session.scalar(select(VectorSyncJob))
    assert job.payload["records"][0]["embedding"] == embedding
    assert job.payload["records"][0]["chunk_id"] == chunk_id
    assert VectorSyncService(postgres_session, vector_store).run_once()
    assert job.status == "succeeded" and job.payload == {}
    restored = postgres_session.get(DocumentChunk, chunk_id)
    assert restored.content == "历史原文保持不变" and restored.keyword_text == "历史 原文"
    assert postgres_session.scalar(text("SELECT count(*) FROM pg_indexes WHERE indexname='ix_document_chunks_keyword_bm25'")) == 1
    assert not postgres_session.scalar(text("SELECT count(*) FROM information_schema.columns WHERE table_name='document_chunks' AND column_name='embedding'"))
    hits = ChunkRepository(postgres_session, vector_store).search(scope=scope, query_embedding=embedding)
    assert len(hits) == 1 and hits[0].chunk_id == chunk_id and hits[0].distance == pytest.approx(0, abs=1e-5)
