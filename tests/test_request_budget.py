from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.knowledge.request_budget import evidence_deadline, remaining_timeout
from backend.app.knowledge.audited_embeddings import EmbeddingClient
from backend.app.knowledge.milvus_store import MilvusVectorStore


def test_deadline_is_local_and_does_not_mutate_shared_clients():
    client = Mock()
    wrapper = EmbeddingClient(client, 'fake', timeout=30)
    wrapper.create(input='a')
    assert 'timeout' not in client.create.call_args.kwargs
    with evidence_deadline(monotonic()+2):
        wrapper.create(input='b')
        assert 0 < client.create.call_args.kwargs['timeout'] <= 2
        transport = Mock()
        transport.post.return_value.json.return_value = {'code':0, 'data':{}}
        store = MilvusVectorStore(SimpleNamespace(milvus_token='fake', milvus_timeout_seconds=10), transport=transport)
        store._call('collections/describe')
        assert sum(transport.post.call_args.kwargs['timeout']) <= 2
    assert remaining_timeout(30) == 30
    wrapper.create(input='c')
    assert 'timeout' not in client.create.call_args.kwargs


def test_expired_budget_prevents_outbound_embedding_call():
    client = Mock()
    with evidence_deadline(monotonic()-1), pytest.raises(TimeoutError):
        EmbeddingClient(client, 'fake').create(input='test')
    client.create.assert_not_called()


def test_deadline_in_one_thread_does_not_affect_other_requests():
    with evidence_deadline(monotonic()+1), ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(remaining_timeout,30).result() == 30
        assert remaining_timeout(30) <= 1
