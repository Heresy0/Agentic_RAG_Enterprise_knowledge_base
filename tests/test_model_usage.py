import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.model_usage import router
from backend.app.db.base import Base
from backend.app.db.models import Tenant, User, KnowledgeBase, KnowledgeBaseUserGrant
from backend.app.db.model_usage_models import ModelUsageEvent, ModelUsageScope
from backend.app.evaluation.trace import capture_trace
from backend.app.evaluation.runner import score_trace
from backend.app.evaluation.reporting import write_report
from backend.app.knowledge.audited_embeddings import AuditedEmbeddings
from backend.app.knowledge.reranker import QwenReranker, RerankError
from backend.app.model_usage import (ModelCall, UsageSink, estimate_cost, invoke_structured,
                                     normalize_usage, parse_prices, summarize_usage, usage_context)
from backend.app.security.dependencies import get_current_principal
from backend.app.security.principal import Principal
from backend.app.security.retrieval_scope import RetrievalScope


def scope():
    return RetrievalScope(uuid4(), uuid4())


@pytest.mark.parametrize("raw,counts,source", [
    ({"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}, (100, 20, 120), "provider"),
    ({"prompt_tokens": 100, "completion_tokens": 20}, (100, 20, 120), "provider_components"),
    ({"total_tokens": 100}, (None, None, 100), "provider"),
    ({}, (None, None, None), "unknown"),
    ({"total_tokens": True}, (None, None, None), "unknown"),
    ({"total_tokens": -1}, (None, None, None), "unknown"),
    ({"total_tokens": "100"}, (None, None, None), "unknown"),
    ({"input_tokens": 10, "output_tokens": 2, "total_tokens": 11}, (None, None, None), "unknown"),
    ({"total_tokens": 0}, (None, None, 0), "provider"),
    ({"total_tokens": 2147483648}, (None, None, None), "unknown"),
    ({"input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
      "input_token_details": {"cache_read": 11}}, (None, None, None), "unknown"),
])
def test_usage_normalization(raw, counts, source):
    row = normalize_usage(raw)
    assert tuple(row[key] for key in ("input_tokens", "output_tokens", "total_tokens")) == counts
    assert row["usage_source"] == source


def test_cache_and_reasoning_are_subsets_not_extra_tokens():
    price = parse_prices(json.dumps({"dashscope:test": dict(version="example-only", currency="CNY",
        input_per_million="2", output_per_million="10", cached_input_per_million="0.5")}))["dashscope:test"]
    usage = normalize_usage(dict(input_tokens=100, output_tokens=20, total_tokens=120,
                                 input_token_details=dict(cache_read=40), output_token_details=dict(reasoning=10)))
    assert usage["total_tokens"] == 120
    assert estimate_cost(usage, price) == Decimal("0.00034")
    assert estimate_cost(usage, {key: value for key, value in price.items() if key != "cached_input_per_million"}) is None
    assert estimate_cost(normalize_usage(dict(total_tokens=120)), price) is None
    total_price = dict(version="v1", currency="CNY", total_per_million="1")
    assert estimate_cost(normalize_usage(dict(total_tokens=120)), total_price) == Decimal("0.00012")
    assert estimate_cost(normalize_usage({}), total_price) is None


@pytest.mark.parametrize("raw", ["[]", "not-json", '{"dashscope:test":{"version":"v1","currency":"CNY","total_per_million":-1}}',
    '{"dashscope:test":{"version":"v1","currency":"CNY","total_per_million":"NaN"}}',
    '{"dashscope:test":{"version":"v1","currency":"CNY","input_per_million":1}}',
    '{"dashscope:test":{"version":"v1","currency":"CNY","total_per_million":1,"secret":"key"}}'])
def test_invalid_prices_rejected_without_echoing_secrets(raw):
    with pytest.raises(ValueError) as error:
        parse_prices(raw)
    assert raw not in str(error.value)


def raw_message():
    return AIMessage(content="PRIVATE RESPONSE", usage_metadata=dict(input_tokens=100, output_tokens=20, total_tokens=120),
                     response_metadata=dict(request_id="supplier-123", private="SECRET"))


def test_structured_parse_failure_keeps_usage_and_never_stores_content():
    model = SimpleNamespace(invoke=lambda messages: dict(raw=raw_message(), parsed=None, parsing_error=ValueError("SECRET")))
    with usage_context(scope=scope()) as context, capture_trace() as trace:
        with pytest.raises(ValueError):
            invoke_structured(model, ["PRIVATE PROMPT"], stage="answer_generation", model_name="test")
    event = context.events[0]
    assert event["status"] == "error" and event["total_tokens"] == 120
    assert event["details"]["error_type"] == "ValueError"
    assert event["details"]["provider_request_id"] == "supplier-123"
    assert not any(secret in json.dumps(context.events) for secret in ("PRIVATE", "SECRET"))
    assert trace.stages["model_usage"] == context.events


def test_success_and_timeout_are_distinct_attempts_and_unknown_is_not_zero():
    with usage_context(scope=scope()) as context:
        with ModelCall("answer_generation", "test") as call:
            call.observe(dict(total_tokens=3))
        with pytest.raises(TimeoutError):
            with ModelCall("answer_generation", "test"):
                raise TimeoutError("PRIVATE URL")
    summary = summarize_usage(context.events + context.events)
    assert summary["calls"] == 2 and summary["reported_total_tokens"] == 3
    assert summary["unknown_usage_calls"] == 1 and summary["unknown_usage_rate"] == .5
    assert context.events[0]["event_id"] != context.events[1]["event_id"]


def test_context_is_nested_request_local_and_reset():
    authorized = scope()
    def run(number):
        with usage_context(scope=authorized, request_id=str(number)) as outer:
            with usage_context(scope=authorized) as inner:
                assert inner is outer
                with ModelCall("rerank", "test") as call:
                    call.observe(dict(total_tokens=number))
            return outer.events
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(run, range(1, 5)))
    assert [row[0]["request_id"] for row in records] == ["1", "2", "3", "4"]
    with usage_context(scope=authorized) as context:
        assert context.events == []
        with pytest.raises(ValueError):
            with usage_context(scope=scope()):
                pass


def test_embedding_records_physical_batches_and_query_stage_without_hidden_retry():
    calls = []
    def create(**kwargs):
        calls.append(kwargs["input"])
        return dict(data=[dict(embedding=[1.0, 2.0]) for _ in kwargs["input"]], usage=dict(total_tokens=len(kwargs["input"])))
    base = OpenAIEmbeddings(model="test", api_key="test-only", base_url="https://unused.invalid/v1",
                            check_embedding_ctx_length=False, chunk_size=10, max_retries=0,
                            client=SimpleNamespace(create=create))
    embedding = AuditedEmbeddings(base)
    with usage_context(scope=scope()) as context:
        assert len(embedding.embed_documents(["private"] * 21)) == 21
        assert embedding.embed_query("private") == [1, 2]
    assert len(calls) == 4
    assert [row["stage"] for row in context.events] == ["document_embedding"] * 3 + ["query_embedding"]
    assert summarize_usage(context.events)["reported_total_tokens"] == 22
    assert all(row["input_tokens"] is None for row in context.events)


def test_rerank_records_usage_before_result_validation(monkeypatch):
    response = SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: dict(usage=dict(total_tokens=40), request_id="rerank-1", results="bad"))
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response)
    reranker = QwenReranker(endpoint="https://unused.invalid", api_key="SECRET")
    with usage_context(scope=scope()) as context:
        with pytest.raises(RerankError):
            reranker.rerank(query="PRIVATE", documents=["PRIVATE"], top_n=1)
        assert reranker.rerank(query="PRIVATE", documents=[], top_n=1) == []
    assert len(context.events) == 1
    assert context.events[0]["total_tokens"] == 40 and context.events[0]["status"] == "error"
    assert "PRIVATE" not in json.dumps(context.events) and "SECRET" not in json.dumps(context.events)


@pytest.fixture
def ledger(tmp_path):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "ledger.sqlite"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_sink_commits_independently_finishes_once_and_crash_stays_pending(ledger):
    authorized = scope()
    with ledger() as business, usage_context(scope=authorized) as context:
        context.sink = UsageSink(ledger)
        with ModelCall("rerank", "test") as call:
            with ledger() as reader:
                assert reader.get(ModelUsageEvent, call.id).status == "pending"
            call.observe(dict(total_tokens=7))
        business.rollback()
        context.sink.finish(call.id, authorized.tenant_id, dict(status="error", total_tokens=999))
        with ledger() as reader:
            row = reader.get(ModelUsageEvent, call.id)
            assert row.status == "success" and row.total_tokens == 7
            assert reader.scalar(select(ModelUsageScope).where(ModelUsageScope.event_id == call.id)).knowledge_base_id == authorized.knowledge_base_id
        pending = ModelCall("rerank", "test")
        pending.__enter__()  # Simulate termination before __exit__.
        with ledger() as reader:
            row = reader.get(ModelUsageEvent, pending.id)
            assert row.status == "pending" and row.total_tokens is None


def test_sink_failure_is_fail_open_sanitized_and_reported(caplog):
    class BrokenSink:
        def begin(self, *args):
            raise RuntimeError("SECRET DATABASE PASSWORD")
    with usage_context(scope=scope()) as context:
        context.sink = BrokenSink()
        with ModelCall("rerank", "test") as call:
            call.observe(dict(total_tokens=2))
    assert context.events[0]["persisted"] is False
    assert "SECRET" not in caplog.text and "RuntimeError" in caplog.text


def test_evaluation_old_usage_is_unobserved_and_failure_cost_is_retained(tmp_path):
    case = dict(id="q1", expected_evidence=[], answerable=True)
    old = score_trace(case, dict(stages={}, error_type="TimeoutError"))
    assert old["metrics"]["model_usage"]["observed"] is False
    assert old["metrics"]["model_usage"]["reported_total_tokens"] is None
    with capture_trace() as trace, usage_context(scope=scope()):
        with ModelCall("rerank", "test") as call:
            call.observe(dict(total_tokens=17))
    scored = score_trace(case, dict(trace.to_dict(), error_type="TimeoutError"))
    usage = scored["metrics"]["model_usage"]
    report = dict(mode="local", layers=dict(answer=dict(status="completed", records=[dict(id="q1", status="error", model_usage=usage)]),
                  conversations=dict(status="completed", records=[dict(id="m1", status="error", turns=[dict(metrics=dict(model_usage=usage))])])))
    write_report(report, tmp_path)
    # Same event appearing in two records must not be charged twice.
    assert report["model_usage"]["calls"] == 1 and report["model_usage"]["reported_total_tokens"] == 17
    assert "模型 Token" in (tmp_path / "report.md").read_text(encoding="utf-8")


@pytest.fixture
def usage_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args=dict(check_same_thread=False), poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    tenant_id, user_id, kb1, kb2 = uuid4(), uuid4(), uuid4(), uuid4()
    principal = Principal(user_id=user_id, tenant_id=tenant_id, external_subject="admin")
    with factory() as session:
        session.add(Tenant(id=tenant_id, name="test")); session.flush()
        session.add_all([User(id=user_id, tenant_id=tenant_id, name="admin", external_subject="admin"),
                        KnowledgeBase(id=kb1, tenant_id=tenant_id, name="A"), KnowledgeBase(id=kb2, tenant_id=tenant_id, name="B")])
        session.flush()
        session.add(KnowledgeBaseUserGrant(tenant_id=tenant_id, user_id=user_id, knowledge_base_id=kb1, permission="admin"))
        session.commit()
    app = FastAPI()
    app.state.database_session_factory = factory
    app.include_router(router)
    app.dependency_overrides[get_current_principal] = lambda: principal
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, factory=factory, tenant=tenant_id, user=user_id, kb1=kb1, kb2=kb2, app=app)
    engine.dispose()


def insert_usage(api, scope, tokens):
    with usage_context(scope=scope) as context:
        context.sink = UsageSink(api.factory)
        with ModelCall("rerank", "test") as call:
            call.observe(dict(total_tokens=tokens))
    return context.events[0]


def test_admin_api_excludes_partial_cross_kb_and_other_tenants_before_limit_and_summary(usage_api):
    api = usage_api
    single = insert_usage(api, RetrievalScope(api.tenant, api.kb1), 11)
    insert_usage(api, RetrievalScope(api.tenant, knowledge_base_ids=frozenset({api.kb1, api.kb2})), 22)
    insert_usage(api, RetrievalScope(uuid4(), api.kb1), 999)
    url = f"/api/knowledge-bases/{api.kb1}/model-usage"
    response = api.client.get(url, params=dict(limit=1))
    assert response.status_code == 200
    assert [row["total_tokens"] for row in response.json()["items"]] == [11]
    groups = api.client.get(url + "/summary").json()["groups"]
    assert len(groups) == 1 and groups[0]["calls"] == 1 and groups[0]["reported_total_tokens"] == 11
    with api.factory() as session:
        session.add(KnowledgeBaseUserGrant(tenant_id=api.tenant, user_id=api.user, knowledge_base_id=api.kb2, permission="admin"))
        session.commit()
    assert [row["total_tokens"] for row in api.client.get(url).json()["items"]] == [22, 11]
    summary = api.client.get(url + "/summary").json()["groups"][0]
    assert summary["calls"] == 2 and summary["reported_total_tokens"] == 33
    assert len(api.client.get(url, params=dict(request_id=single["request_id"])).json()["items"]) == 1


def test_admin_api_rejects_editor_inactive_identity_and_invalid_window(usage_api):
    api = usage_api
    url = f"/api/knowledge-bases/{api.kb1}/model-usage"
    assert api.client.get(url, params=dict(since="2026-01-01T00:00:00Z", until="2026-10-01T00:00:00Z")).status_code == 422
    assert api.client.get(url, params=dict(before="2026-10-01T00:00:00Z")).status_code == 422
    with api.factory() as session:
        grant = session.scalar(select(KnowledgeBaseUserGrant))
        grant.permission = "editor"; session.commit()
    assert api.client.get(url).status_code == 403
    assert api.client.get(url + "/summary").status_code == 403
