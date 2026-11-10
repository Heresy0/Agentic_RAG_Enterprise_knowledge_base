"""A typed vector outage stops primary retrieval, but not safe optional fallback."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from test_document_upload_api import api_context
from test_manufacturing_m3_api import harness, ask
from test_evidence_coordinator import setup as agent_setup, collect
from test_evidence_feedback import setup as feedback_setup, execute as feedback, outcome
from backend.app.db.quality_models import QARun
from backend.app.knowledge.milvus_store import VectorStoreUnavailable
from backend.app.manufacturing.scope import ManufacturingScopeChanged
from backend.app.security.authorization import AuthorizationDenied

PRIVATE = "synthetic-vector-private-response"


def test_http_primary_vector_outage_is_safe_503_without_model_or_success(harness, monkeypatch, caplog):
    h = harness
    def unavailable(*a, **kw):
        raise VectorStoreUnavailable(PRIVATE)
    monkeypatch.setattr(h.answer._retrieval_service._vector_store, "search", unavailable)
    with TestClient(h.c.app) as client:
        response = ask(client, h, question="首件要求是什么？")
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["error_code"] == "manufacturing_retrieval_unavailable"
    assert response.headers["x-request-id"] == response.json()["detail"]["request_id"]
    assert PRIVATE not in response.text + caplog.text and not h.generation.calls
    with h.c.session_factory() as session:
        assert session.scalars(select(QARun)).all() == []


@pytest.mark.parametrize("change", [None, "revoke", "source"])
def test_agent_vector_outage_rechecks_access_and_sources_before_fallback(change):
    scope, retrieval, guard, _, coordinator = agent_setup(actions=[
        dict(action="search", reason="missing_evidence", query="补查工艺温度")])
    original = retrieval.search
    def search(query, **kwargs):
        if retrieval.searches:
            if change == "revoke": guard.allowed = False
            raise VectorStoreUnavailable(PRIVATE)
        return original(query, **kwargs)
    retrieval.search = search
    refresh = retrieval.refresh_evidence
    def current(*a, **kw):
        if change == "source" and len(retrieval.searches) and len(coordinator.decider.calls):
            raise ManufacturingScopeChanged("rule withdrawn")
        return refresh(*a, **kw)
    retrieval.refresh_evidence = current
    if change:
        with pytest.raises(AuthorizationDenied if change == "revoke" else ManufacturingScopeChanged):
            collect(scope, coordinator)
    else:
        result, trace = collect(scope, coordinator)
        assert [(doc.id, doc.page_content) for doc in result.documents] == [
            (doc.id, doc.page_content) for doc in retrieval.initial]
        assert trace["stages"]["agent_decisions"][-1]["outcome"] == "fallback"


@pytest.mark.parametrize("revoke", [False, True])
def test_feedback_vector_outage_keeps_refusal_without_regeneration(monkeypatch, revoke):
    def hook(guard, count):
        if count == 2:
            if revoke: guard.allowed = False
            raise VectorStoreUnavailable(PRIVATE)
    service, retrieval, _, calls, _, _ = feedback_setup(monkeypatch, search_hook=hook)
    if revoke:
        with pytest.raises(AuthorizationDenied): feedback(service)
    else:
        result, trace = feedback(service)
        assert not result.answerable and not result.citations and outcome(trace) == "fallback"
    assert len(retrieval.calls) == 2 and calls == []
