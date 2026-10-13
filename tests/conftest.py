import pytest

from fake_vector_store import FakeVectorStore


@pytest.fixture(autouse=True)
def indexing_vector_store(request, monkeypatch):
    # Only pure indexing unit tests replace the constructor. Integration tests use Milvus.
    if request.module.__name__ not in ("test_document_indexing_service", "test_document_candidate_indexing",
                                       "test_vector_sync_service"):
        return None
    from backend.app.indexing import document_indexing_service
    store = FakeVectorStore()
    monkeypatch.setattr(document_indexing_service, "MilvusVectorStore", lambda _settings: store)
    return store
