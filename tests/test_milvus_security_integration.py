"""Native RBAC and scope checks, using only synthetic vectors and owned IDs."""
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import requests

from backend.app.config import read_milvus_token_file, required_env
from backend.app.knowledge.milvus_store import MilvusVectorStore, vector_record_id
from backend.app.security.retrieval_scope import RetrievalScope

pytestmark = pytest.mark.integration


@pytest.fixture
def clients():
    if os.getenv("RUN_MILVUS_SECURITY_TESTS") != "1":
        pytest.skip("未启用 Milvus 原生认证/授权集成测试")
    # Native database checks need no model configuration or live API keys in CI.
    def store(filename):
        return MilvusVectorStore(SimpleNamespace(milvus_uri=os.getenv("MILVUS_URI", "http://127.0.0.1:19531"),
            milvus_collection=os.getenv("MILVUS_COLLECTION", "enterprise_chunks"), milvus_timeout_seconds=20,
            milvus_token=read_milvus_token_file(required_env(filename))))
    return (store("MILVUS_INTEGRATION_ADMIN_TOKEN_FILE"), store("MILVUS_TOKEN_FILE"),
            store("MILVUS_WORKER_TOKEN_FILE"))


def response(store, operation, token=None, **body):
    headers = {"Authorization": "Bearer " + (store.token if token is None else token)}
    if token == "":
        headers = {}
    result = requests.post(store.uri + "/v2/vectordb/" + operation,
        json={"collectionName": store.collection, **body}, headers=headers, timeout=20)
    return result.status_code, result.json()


def denied(store, operation, **body):
    status, result = response(store, operation, **body)
    assert result.get("code") != 0
    # A malformed request / missing collection is not evidence of RBAC enforcement.
    message = result.get("message", "").lower()
    assert "privilege" in message or "permission" in message, (status, result.get("code"), message)


@pytest.mark.parametrize("token", ["", "kb_api:incorrect-password", "root:Milvus"])
def test_anonymous_wrong_password_and_vendor_default_rejected(clients, token):
    _, reader, _ = clients
    status, result = response(reader, "entities/query", token=token,
        filter='id != ""', outputFields=["count(*)"])
    assert status == 401 and result["code"] == 1800


def row(tenant, kb, document, generation):
    chunk = uuid4().hex + uuid4().hex
    return dict(id=vector_record_id(chunk, generation), chunk_id=chunk, tenant_id=str(tenant),
        knowledge_base_id=str(kb), document_id=str(document), generation=generation,
        created_at=2**31, embedding=[1.0] + [0.0] * 1023)


def test_service_write_read_and_tenant_kb_generation_isolation(clients):
    admin, reader, writer = clients
    tenant, other_tenant, kb, other_kb, document = [uuid4() for _ in range(5)]
    generation, unpublished = uuid4().hex, uuid4().hex
    rows = [row(tenant, kb, document, generation), row(tenant, other_kb, uuid4(), generation),
            row(other_tenant, kb, uuid4(), generation), row(tenant, kb, document, unpublished)]
    ids = [r["id"] for r in rows]
    import json
    cleanup = "id in " + json.dumps(ids)
    count_before = admin.count('id != ""')
    try:
        for item in rows:
            writer.upsert([item])
        scope = RetrievalScope(tenant, knowledge_base_ids=[kb])
        hits = reader.search(scope=scope, generations=[generation], vector=rows[0]["embedding"], limit=10)
        assert [hit["chunk_id"] for hit in hits] == [rows[0]["chunk_id"]]
        assert reader.search(scope=RetrievalScope(tenant), generations=[generation], vector=[], limit=10) == []
        assert reader.search(scope=scope, generations=[], vector=[], limit=10) == []
        denied(reader, "entities/upsert", data=[rows[0]])
        denied(reader, "entities/delete", filter=cleanup)
        denied(reader, "collections/load")
        writer.delete(scope=scope, document_id=document)
        assert reader.count('document_id == "' + str(document) + '"') == 0
    finally:
        admin._call("entities/delete", filter=cleanup)
        assert admin.count('id != ""') == count_before


def test_service_accounts_cannot_read_other_collection_or_manage_users_schema(clients):
    admin, reader, writer = clients
    name = "enterprise_security_test_" + uuid4().hex
    user = "kb_probe_" + uuid4().hex[:16]
    shadow = MilvusVectorStore(SimpleNamespace(milvus_uri=admin.uri, milvus_collection=name,
        milvus_token=admin.token, milvus_timeout_seconds=20))
    assert name.startswith("enterprise_security_test_")
    assert user not in admin._call("users/list")
    try:
        shadow.ensure_collection()
        for service in (reader, writer):
            denied(service, "entities/query", collectionName=name, filter='id != ""', outputFields=["count(*)"])
            denied(service, "collections/drop", collectionName=name)
            denied(service, "users/create", userName=user, password="test-only-random-" + uuid4().hex)
            # Reassigning an existing read role is harmless even if a regression
            # permits it, while still exercising the native role-management gate.
            denied(service, "users/grant_role", userName="kb_api", roleName="kb_api_read")
        # Read/write worker can operate data, but cannot create a collection.
        denied(writer, "collections/create", collectionName=name + "_extra",
               dimension=1024, metricType="COSINE")
    finally:
        for collection in (name, name + "_extra"):
            if admin._call("collections/has", collectionName=collection).get("has"):
                admin._call("collections/drop", collectionName=collection)
        if user in admin._call("users/list"):
            admin._call("users/drop", userName=user)
