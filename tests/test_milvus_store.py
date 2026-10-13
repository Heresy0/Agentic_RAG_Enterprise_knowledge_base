from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests

from backend.app.knowledge.milvus_store import MilvusVectorStore, VectorStoreUnavailable, vector_record_id
from backend.app.security.retrieval_scope import RetrievalScope


def store():
    transport = Mock()
    transport.post.return_value.json.return_value = {"code": 0, "data": []}
    return MilvusVectorStore(SimpleNamespace(milvus_uri="http://localhost:19531", milvus_collection="test_chunks",
        milvus_timeout_seconds=10, milvus_token="test-token"), transport=transport), transport


def record():
    row = dict(chunk_id="c" * 64, generation=uuid4().hex, tenant_id=str(uuid4()),
               knowledge_base_id=str(uuid4()), document_id=str(uuid4()), created_at=1,
               embedding=[1.0] + [0.0] * 1023)
    row["id"] = vector_record_id(row["chunk_id"], row["generation"])
    return row


def test_ann_filter_contains_tenant_kbs_and_only_published_generations():
    vector_store, transport = store()
    scope = RetrievalScope(uuid4(), knowledge_base_ids=[uuid4(), uuid4()])
    generation = uuid4().hex
    vector_store.search(scope=scope, generations=[generation], vector=[1.0] + [0.0] * 1023, limit=8)
    request = transport.post.call_args.kwargs
    body = request["json"]
    assert str(scope.tenant_id) in body["filter"]
    assert all(str(kb) in body["filter"] for kb in scope.knowledge_base_ids)
    assert generation in body["filter"] and "generation in" in body["filter"]
    assert body["limit"] == 8 and body["consistencyLevel"] == "Strong"
    assert body["searchParams"]["metricType"] == "COSINE"
    assert request["timeout"] == (3, 10)
    assert request["headers"]["Authorization"] == "Bearer test-token"


def test_empty_authorization_never_calls_milvus():
    vector_store, transport = store()
    scope = RetrievalScope(uuid4())
    assert vector_store.search(scope=scope, generations=[uuid4().hex], vector=[], limit=8) == []
    with pytest.raises(ValueError):
        vector_store.delete(scope=scope, document_id=uuid4())
    transport.post.assert_not_called()


@pytest.mark.parametrize("change", ["nan", "dimension", "identity", "generation", "mixed", "duplicate"])
def test_invalid_records_rejected_before_network(change):
    vector_store, transport = store()
    row = record()
    rows = [row]
    if change == "nan":
        row["embedding"][0] = float("nan")
    elif change == "dimension":
        row["embedding"] = [1.0]
    elif change == "identity":
        row["id"] = "forged"
    elif change == "generation":
        row["generation"] = '" or true'
    elif change == "mixed":
        rows.append(record())
    else:
        rows.append(dict(row))
    with pytest.raises(ValueError):
        vector_store.upsert(rows)
    transport.post.assert_not_called()


def test_partial_write_or_invisible_rows_prevent_publication():
    vector_store, transport = store()
    transport.post.return_value.json.return_value = {"code": 0, "data": {"upsertCount": 0}}
    with pytest.raises(VectorStoreUnavailable):
        vector_store.upsert([record()])
    transport.post.return_value.json.side_effect = [
        {"code": 0, "data": {"upsertCount": 1}}, {"code": 0, "data": [{"count(*)": 0}]}]
    with pytest.raises(VectorStoreUnavailable):
        vector_store.upsert([record()])


def test_remote_errors_are_sanitized_and_not_silently_empty():
    vector_store, transport = store()
    transport.post.side_effect = requests.ConnectionError("sensitive server detail")
    with pytest.raises(VectorStoreUnavailable) as error:
        vector_store.count('id == "x"')
    assert "sensitive" not in str(error.value)
    assert "test-token" not in str(error.value)


def test_generation_namespacing_keeps_candidate_from_overwriting_current_vector():
    chunk_id = "c" * 64
    assert vector_record_id(chunk_id, "a" * 32) != vector_record_id(chunk_id, "b" * 32)
