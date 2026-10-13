from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import StaticPool

from backend.app.api.knowledge_bases import (
    router as knowledge_base_router,
)
from backend.app.db.base import Base
from backend.app.db.models import (
    Department,
    DepartmentMembership,
    KnowledgeBase,
    KnowledgeBaseDepartmentGrant,
    KnowledgeBaseUserGrant,
    KnowledgeDocument,
    Tenant,
    User,
)
from backend.app.db.session import (
    create_session_factory,
)
from backend.app.security.dependencies import (
    get_current_principal,
)
from backend.app.security.principal import Principal


@pytest.fixture
def api_context() -> Iterator[dict]:
    engine: Engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={
            "check_same_thread": False,
        },
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(
        dbapi_connection,
        _connection_record,
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    tenant_id = uuid4()
    alice_id = uuid4()
    technology_id = uuid4()

    company_kb_id = uuid4()
    technology_kb_id = uuid4()
    hr_kb_id = uuid4()
    special_kb_id = uuid4()
    technology_document_id = uuid4()
    company_document_id = uuid4()

    with session_factory() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name="测试企业",
            )
        )
        session.flush()

        session.add_all([
            User(
                id=alice_id,
                tenant_id=tenant_id,
                external_subject="alice",
                name="Alice",
            ),
            Department(
                id=technology_id,
                tenant_id=tenant_id,
                name="技术部",
            ),
            KnowledgeBase(
                id=company_kb_id,
                tenant_id=tenant_id,
                name="公司公共知识库",
                visibility="company",
            ),
            KnowledgeBase(
                id=technology_kb_id,
                tenant_id=tenant_id,
                name="技术部知识库",
                visibility="restricted",
            ),
            KnowledgeBase(
                id=hr_kb_id,
                tenant_id=tenant_id,
                name="人力资源部知识库",
                visibility="restricted",
            ),
            KnowledgeBase(
                id=special_kb_id,
                tenant_id=tenant_id,
                name="专项知识库",
                visibility="restricted",
            ),
        ])
        session.flush()

        session.add_all([
            DepartmentMembership(
                tenant_id=tenant_id,
                department_id=technology_id,
                user_id=alice_id,
            ),
            KnowledgeBaseDepartmentGrant(
                tenant_id=tenant_id,
                knowledge_base_id=technology_kb_id,
                department_id=technology_id,
                permission="viewer",
            ),
            KnowledgeBaseUserGrant(
                tenant_id=tenant_id,
                knowledge_base_id=special_kb_id,
                user_id=alice_id,
                permission="editor",
            ),
        ])

        session.add_all([
            KnowledgeDocument(
                id=technology_document_id,
                tenant_id=tenant_id,
                knowledge_base_id=technology_kb_id,
                created_by_user_id=alice_id,
                source_id="technology-document",
                file_name="技术手册.docx",
                storage_uri=(
                    "file:///docs/technology.docx"
                ),
                mime_type=(
                    "application/vnd.openxmlformats-"
                    "officedocument.wordprocessingml."
                    "document"
                ),
                content_hash="a" * 64,
                status="ready",
                version=1,
                metadata_json={
                    "chunk_count": 15,
                },
            ),
            KnowledgeDocument(
                id=company_document_id,
                tenant_id=tenant_id,
                knowledge_base_id=company_kb_id,
                created_by_user_id=alice_id,
                source_id="company-document",
                file_name="公司制度.docx",
                storage_uri=(
                    "file:///docs/company.docx"
                ),
                mime_type=(
                    "application/vnd.openxmlformats-"
                    "officedocument.wordprocessingml."
                    "document"
                ),
                content_hash="b" * 64,
                status="ready",
                version=1,
                metadata_json={
                    "chunk_count": 8,
                },
            ),
        ])
        session.commit()

    app = FastAPI()
    app.state.database_session_factory = (
        session_factory
    )
    app.include_router(knowledge_base_router)

    try:
        yield {
            "app": app,
            "principal": Principal(
                user_id=alice_id,
                tenant_id=tenant_id,
                external_subject="alice",
            ),
            "technology_kb_id": technology_kb_id,
            "hr_kb_id": hr_kb_id,
            "special_kb_id": special_kb_id,
            "technology_document_id": (
                technology_document_id
            ),
        }

    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_requires_authentication(
    api_context: dict,
) -> None:
    client = TestClient(api_context["app"])

    response = client.get(
        "/api/knowledge-bases"
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "未认证。"


def test_lists_only_accessible_knowledge_bases(
    api_context: dict,
) -> None:
    app = api_context["app"]

    app.dependency_overrides[
        get_current_principal
    ] = lambda: api_context["principal"]

    try:
        client = TestClient(app)

        response = client.get(
            "/api/knowledge-bases"
        )

    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200

    items = response.json()["items"]

    permissions = {
        item["name"]: item["permission"]
        for item in items
    }

    assert permissions == {
        "公司公共知识库": "viewer",
        "技术部知识库": "viewer",
        "专项知识库": "editor",
    }

    assert "人力资源部知识库" not in permissions


def test_lists_documents_only_in_requested_knowledge_base(
    api_context: dict,
) -> None:
    app = api_context["app"]
    app.dependency_overrides[
        get_current_principal
    ] = lambda: api_context["principal"]

    try:
        client = TestClient(app)

        response = client.get(
            "/api/knowledge-bases/"
            f"{api_context['technology_kb_id']}"
            "/documents"
        )

    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200

    items = response.json()["items"]
    assert len(items) == 1

    document = items[0]

    assert document["id"] == str(
        api_context["technology_document_id"]
    )
    assert document["file_name"] == "技术手册.docx"
    assert document["status"] == "ready"
    assert document["version"] == 1
    assert document["chunk_count"] == 15
    assert document["created_at"]
    assert document["updated_at"]
    assert "storage_uri" not in document
    assert "content_hash" not in document


def test_lists_empty_documents_for_accessible_knowledge_base(
    api_context: dict,
) -> None:
    app = api_context["app"]
    app.dependency_overrides[
        get_current_principal
    ] = lambda: api_context["principal"]

    try:
        client = TestClient(app)

        response = client.get(
            "/api/knowledge-bases/"
            f"{api_context['special_kb_id']}"
            "/documents"
        )

    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
    }


def test_rejects_document_list_for_unauthorized_kb(
    api_context: dict,
) -> None:
    app = api_context["app"]
    app.dependency_overrides[
        get_current_principal
    ] = lambda: api_context["principal"]

    try:
        client = TestClient(app)

        response = client.get(
            "/api/knowledge-bases/"
            f"{api_context['hr_kb_id']}"
            "/documents"
        )

    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json() == {
        "detail": "没有权限访问该知识库。",
    }


def test_citation_preview_reauthorizes_and_rejects_stale_or_wrong_document(api_context):
    from backend.app.db.models import DocumentChunk, EMBEDDING_DIMENSION
    app = api_context["app"]
    app.dependency_overrides[get_current_principal] = lambda: api_context["principal"]
    kb_id, doc_id = api_context["technology_kb_id"], api_context["technology_document_id"]
    chunk_id = "a" * 64
    with app.state.database_session_factory() as session:
        session.add(DocumentChunk(id=chunk_id, tenant_id=api_context["principal"].tenant_id,
            knowledge_base_id=kb_id, document_id=doc_id, chunk_index=0, content="<script>原文</script>",
            content_hash="b" * 64, chunking_version="test", embedding_model="test",
            vector_generation="a" * 32, metadata_json={"section_path": "重试规则"}))
        session.commit()
    client = TestClient(app)
    path = f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/chunks/{chunk_id}"
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["content"] == "<script>原文</script>"
    assert client.get(path.replace(str(doc_id), str(uuid4()))).status_code == 404
    assert client.get(path.replace(str(kb_id), str(api_context["hr_kb_id"]))).status_code == 403
    with app.state.database_session_factory() as session:
        session.query(Department).first().status = "disabled"
        session.commit()
    assert client.get(path).status_code == 403
    with app.state.database_session_factory() as session:
        session.query(Department).first().status = "active"
        session.delete(session.get(DocumentChunk, chunk_id))
        session.commit()
    assert client.get(path).status_code == 404
