"""Manufacturing HTTP behavior with real connectors/SQL/RAG and fake model/Redis."""
import asyncio
from copy import deepcopy
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from test_document_upload_api import api_context, _set_principal
from manufacturing_m3_helpers import configure, qa_input, TEST_SECRET
from backend.app.db.business_models import BusinessScopeGrant, ConversationBusinessBinding, QARunBusinessScope
from backend.app.db.conversation_models import Conversation, ConversationTurn
from backend.app.db.models import KnowledgeBaseUserGrant, KnowledgeDocument
from backend.app.db.quality_models import QARun
from backend.app.security.dependencies import get_current_principal
from backend.app.governance.tenant_rate_limiter import RateLimitDecision


@pytest.fixture
def harness(api_context, monkeypatch):
    h = configure(api_context, monkeypatch)
    yield h
    asyncio.run(h.connector.aclose())


def ask(client, h, **kwargs):
    return client.post("/api/manufacturing/qa", json=qa_input(h, **kwargs))


def create(client, h, wo="WO-018"):
    response = client.post("/api/manufacturing/conversations", json=dict(work_order_id=wo,
        knowledge_base_id=str(h.c.knowledge_base_id), expected_source_revision="r1"))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_fact_without_document_uses_business_citation_and_three_reads(harness):
    h = harness
    with TestClient(h.c.app) as client:
        response = ask(client, h, wo="WO-021")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["answerable"] and body["citations"] == []
        assert body["business_citations"][0]["field_name"] == "product_model"
        assert body["business_citations"][0]["value"] == "HC-A18"
        assert body["claim_bindings"][0]["business_citations"] == ["业务2"]
        assert body["snapshot_status"] == "captured"
        assert response.headers["cache-control"] == "no-store" and response.headers["x-request-id"]
        assert len(h.source.reads) == 3 and len(h.generation.calls) == 1
        snapshot = client.get(f"/api/qa/runs/{body['run_id']}")
        assert snapshot.status_code == 200
        frozen = snapshot.json()["snapshot"]
        assert frozen["schema_version"] == 3 and frozen["manufacturing"]["validation"]["mes_reads"] == 3
        assert TEST_SECRET not in snapshot.text and "Authorization" not in snapshot.text
    with h.c.session_factory() as session:
        assert session.scalar(select(QARunBusinessScope.work_order_id)) == "WO-021"


@pytest.mark.parametrize("wo,revision", [("WO-017", "2.0"), ("WO-018", "1.0"), ("WO-019", "2.0")])
def test_version_answer_has_document_and_business_bindings(harness, wo, revision):
    h = harness; h.generation.mode = "applicability"
    with TestClient(h.c.app) as client:
        response = ask(client, h, wo=wo, question="这个工单适用哪个指导书版本？")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["answerable"], body
        assert body["applicability"]["business_revision"] == revision
        assert body["claim_bindings"][0]["document_citations"]
        assert set(body["claim_bindings"][0]["business_citations"]) == {"业务1", "业务2", "业务3", "业务4"}
        assert all(item["document_version"] == 1 for item in body["citations"])
        assert len(h.source.reads) == 3


@pytest.mark.parametrize("wo,behavior", [("WO-020", "clarify"), ("WO-021", "refuse"), ("WO-024", "refuse")])
def test_missing_conditions_no_evidence_and_conflict_never_generate(harness, wo, behavior):
    h = harness
    with TestClient(h.c.app) as client:
        response = ask(client, h, wo=wo, question="这个工单适用哪个版本，首件要求是什么？")
        assert response.status_code == 200, response.text
        body = response.json(); assert body["response_type"] == behavior and not body["answerable"]
        assert not h.generation.calls and len(h.source.reads) == 3
        if wo == "WO-024":
            assert len(body["applicability"]["evidence"]) == 2
            frozen = client.get(f"/api/qa/runs/{body['run_id']}").json()["snapshot"]
            assert len(frozen["manufacturing"]["rule_sources"]) == 2


def test_explicit_version_comparison_keeps_both_sources_without_changing_work_order_selection(harness):
    h = harness; h.generation.mode = "comparison"
    with TestClient(h.c.app) as client:
        response = ask(client, h, question="两版指导书的首件数量有什么差异？")
        assert response.status_code == 200, response.text
        body = response.json(); assert body["answerable"], body
        assert body["applicability"]["business_revision"] == "1.0"
        assert {citation["business_revision"] for citation in body["citations"]} == {"1.0", "2.0"}
        assert all(binding["claim_kind"] == "version_comparison" for binding in body["claim_bindings"])


@pytest.mark.parametrize("at,change,status,generations", [(2, "revision", 409, 0), (3, "revision", 409, 1),
    (2, "same_revision_body", 502, 0), (3, "same_revision_body", 502, 1), (2, "unavailable", 503, 0), (3, "unavailable", 503, 1)])
def test_source_change_or_failure_discards_answer_and_never_retries(harness, at, change, status, generations):
    h = harness
    def hook(source, count):
        if count != at: return
        if change == "revision": source.records["WO-018"]["source_revision"] = "r2"
        elif change == "same_revision_body": source.records["WO-018"]["product_model"] = "CHANGED-PRIVATE"
        else: source.failure = lambda req: httpx.Response(503, text="private-source-error-token", request=req)
    h.source.hook = hook
    with TestClient(h.c.app) as client:
        response = ask(client, h)
        assert response.status_code == status, response.text
        assert response.json()["detail"]["error_code"]
        assert "CHANGED-PRIVATE" not in response.text and "private-source-error" not in response.text
    assert len(h.generation.calls) == generations and len(h.source.reads) == at
    with h.c.session_factory() as session:
        assert session.scalars(select(QARun)).all() == []
        assert session.scalars(select(ConversationTurn)).all() == []


@pytest.mark.parametrize("kind", ["business", "document", "rule"])
def test_generation_time_revocation_or_rule_withdrawal_stops_delivery(harness, kind):
    h = harness
    def revoke(active):
        with h.c.session_factory() as session:
            if kind == "business": session.execute(delete(BusinessScopeGrant))
            elif kind == "document": session.execute(delete(KnowledgeBaseUserGrant))
            else:
                doc = session.get(KnowledgeDocument, h.docs["ecn"][0].id)
                doc.metadata_json = {**doc.metadata_json, "publication": {"state": "withdrawn", "revision": 2}}
            session.commit()
    h.generation.hook = revoke
    with TestClient(h.c.app) as client:
        response = ask(client, h)
        assert response.status_code == {"business": 404, "document": 403, "rule": 409}[kind], response.text
    assert len(h.generation.calls) == 1
    with h.c.session_factory() as session: assert session.scalars(select(QARun)).all() == []


def test_business_list_pagination_and_context_have_uniform_visibility(harness):
    h = harness
    with TestClient(h.c.app) as client:
        first = client.get("/api/manufacturing/work-orders?limit=2")
        assert first.status_code == 200, first.text
        assert len(first.json()["items"]) == 2 and "total" not in first.json()
        second = client.get("/api/manufacturing/work-orders", params=dict(limit=2, cursor=first.json()["next_cursor"]))
        assert second.status_code == 200
        assert not {row["work_order_id"] for row in first.json()["items"]} & {row["work_order_id"] for row in second.json()["items"]}
        hidden = client.get("/api/manufacturing/work-orders/WO-022/context")
        missing = client.get("/api/manufacturing/work-orders/MISSING/context")
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json()["detail"]["error_code"] == missing.json()["detail"]["error_code"]


@pytest.mark.parametrize("extra", [dict(actual_started_at="2026-09-01"), dict(tenant_id=str(uuid4())),
    dict(source_system_id="injected"), dict(base_url="http://evil.invalid"), dict(plant_code="HC-P02")])
def test_client_cannot_override_identity_or_source_facts(harness, extra):
    h = harness
    with TestClient(h.c.app) as client:
        response = client.post("/api/manufacturing/qa", json={**qa_input(h), **extra})
        assert response.status_code == 422
    assert not h.source.reads and not h.generation.calls


def test_missing_auth_business_grant_and_rate_limit_do_not_generate(harness):
    h = harness
    with TestClient(h.c.app) as client:
        _set_principal(h.c, h.c.viewer)
        assert ask(client, h).status_code == 404
        _set_principal(h.c, h.c.editor)
        h.c.app.state.tenant_rate_limiter.decision = RateLimitDecision(False, 1, 0, 4)
        limited = ask(client, h); assert limited.status_code == 429 and limited.headers["retry-after"] == "4"
        h.c.app.dependency_overrides.pop(get_current_principal)
        assert ask(client, h).status_code == 401
        assert client.get("/api/manufacturing/work-orders").status_code == 401
    assert not h.source.reads and not h.generation.calls


@pytest.mark.parametrize("field", ["route_revision", "source_record_id", "source_updated_at", "fetched_at"])
def test_source_fields_have_individual_business_citations_even_without_documents(harness, field):
    h = harness; h.generation.field = field
    with TestClient(h.c.app) as client:
        response = ask(client, h, wo="WO-021", question="工单来源与路线是什么？")
        assert response.status_code == 200, response.text
        body = response.json(); assert body["answerable"], body
        citation = body["business_citations"][0]
        assert citation["field_name"] == field
        assert citation["value"] == body["work_order_context"][field]
        assert len(h.source.reads) == 3


@pytest.mark.parametrize("change", ["source", "business_grant"])
def test_changes_during_retrieval_are_checked_before_main_generation(harness, change):
    h = harness; h.generation.mode = "applicability"
    original = h.answer._evidence_coordinator.collect
    def collect(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "source": h.source.records["WO-018"]["source_revision"] = "r2"
        else:
            with h.c.session_factory() as session:
                session.execute(delete(BusinessScopeGrant)); session.commit()
        return result
    h.answer._evidence_coordinator.collect = collect
    with TestClient(h.c.app) as client:
        response = ask(client, h, question="这个工单适用哪个指导书版本？")
        assert response.status_code == (409 if change == "source" else 404), response.text
    assert not h.generation.calls


def test_disabled_source_returns_structured_error_without_model_call(harness):
    h = harness; h.c.app.state.mes_connectors = {}
    with TestClient(h.c.app) as client:
        response = ask(client, h)
        assert response.status_code == 503 and response.json()["detail"]["error_code"] == "mes_unavailable"
    assert not h.generation.calls and not h.source.reads


def test_reflected_service_credential_is_never_exposed_as_a_business_field(harness):
    h = harness; h.source.records["WO-018"]["status"] = TEST_SECRET
    with TestClient(h.c.app) as client:
        response = ask(client, h)
        assert response.status_code == 502 and TEST_SECRET not in response.text
        assert client.get("/api/manufacturing/work-orders/WO-018/context").status_code == 502
        listing = client.get("/api/manufacturing/work-orders")
        assert listing.status_code == 502 and TEST_SECRET not in listing.text
    assert not h.generation.calls


def test_malformed_model_output_is_a_service_failure_and_not_a_client_input_error(harness):
    h = harness
    def malformed(messages):
        return dict(answerable=True, response_type="answer", claims=[dict(text="missing kind", citations=["业务2"])],
            coverage=[dict(requirement_id="Q1", aspect="型号", status="answered", claim_indices=[1], missing_information=None)])
    h.generation.invoke = malformed
    with TestClient(h.c.app) as client:
        response = ask(client, h)
        assert response.status_code == 503 and response.json()["detail"]["error_code"] == "manufacturing_generation_invalid"
    with h.c.session_factory() as session: assert session.scalars(select(QARun)).all() == []
