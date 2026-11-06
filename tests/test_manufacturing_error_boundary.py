"""One safe HTTP boundary distinguishes business failures from internal bugs."""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from test_document_upload_api import api_context
from test_manufacturing_m3_api import harness, ask, create
from backend.app.api import manufacturing as api
from backend.app.db.models import AuditEvent
from backend.app.db.quality_models import QARun
from backend.app.evaluation.trace import capture_trace
from backend.app.integrations.mes import MESError, MESUnavailable
from backend.app.manufacturing.errors import ManufacturingInputError
from backend.app.manufacturing.scope import ManufacturingScopeChanged
from backend.app.knowledge.milvus_store import VectorStoreUnavailable
from backend.app.quality.eval_budget import EvaluationStopped
from backend.app.security.authorization import AuthorizationDenied
from backend.app.security.business_scope import BusinessAuthorizationDenied

PRIVATE = "synthetic-provider-secret-must-not-be-logged"


@pytest.mark.parametrize("error,status,code", [
    (ManufacturingInputError, 422, "manufacturing_input_invalid"),
    (BusinessAuthorizationDenied, 404, "work_order_unavailable"),
    (AuthorizationDenied, 403, "document_access_changed"),
    (ManufacturingScopeChanged, 409, "manufacturing_scope_changed"),
    (MESError, 502, "mes_invalid_response"),
    (MESUnavailable, 503, "mes_unavailable"),
    (TimeoutError, 503, "manufacturing_generation_unavailable"),
    (SQLAlchemyError, 503, "manufacturing_storage_unavailable"),
    (EvaluationStopped, 503, "manufacturing_evaluation_stopped"),
    (VectorStoreUnavailable, 503, "manufacturing_retrieval_unavailable"),
    (ValueError, 500, "manufacturing_internal_error"),
    (TypeError, 500, "manufacturing_internal_error"),
    (RuntimeError, 500, "manufacturing_internal_error"),
])
def test_explicit_failure_classification_never_echoes_exception_text(error, status, code, caplog):
    exc = error() if error in (ManufacturingInputError, BusinessAuthorizationDenied, MESError, MESUnavailable) else error(PRIVATE)
    response = api.fail(exc, "review-request")
    assert response.status_code == status and response.detail["error_code"] == code
    assert response.headers["X-Request-ID"] == response.detail["request_id"] == "review-request"
    assert response.headers["Cache-Control"] == "no-store"
    assert PRIVATE not in json.dumps(response.detail) + caplog.text


@pytest.mark.parametrize("error", [ValueError, RuntimeError, TypeError, KeyError])
def test_internal_model_failure_is_logged_safely_and_does_not_save_success(harness, error, caplog):
    h = harness
    def broken(_):
        try:
            raise RuntimeError(PRIVATE + "-cause")
        except RuntimeError as cause:
            raise error(PRIVATE) from cause
    h.generation.hook = broken
    with TestClient(h.c.app) as client, capture_trace() as trace:
        response = ask(client, h)
    assert response.status_code == 500, response.text
    request_id = response.headers["X-Request-ID"]
    assert response.json()["detail"]["request_id"] == request_id
    logs = [json.loads(record.message) for record in caplog.records if record.name == api.__name__]
    event = next(row for row in logs if row["event"] == "manufacturing.internal_error")
    assert event["request_id"] == request_id and event["error_type"] == error.__name__
    assert event["frames"][-1]["function"] == "broken"
    assert all(set(frame) == {"file", "line", "function"} for frame in event["frames"])
    assert PRIVATE not in response.text + caplog.text
    with h.c.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(QARun)) == 0
        audit = session.scalar(select(AuditEvent).where(AuditEvent.request_id == request_id))
        assert audit.outcome == "failed" and audit.details_json["error_type"] == error.__name__
    usage = trace.to_dict()["stages"]["model_usage"]
    assert len(usage) == 1 and usage[0]["status"] == "error"


@pytest.mark.parametrize("method,path", [
    ("GET", "/work-orders"), ("GET", "/work-orders/WO-018/context"),
    ("GET", "/conversations"), ("GET", "/conversations/" + str(uuid4())),
    ("DELETE", "/conversations/" + str(uuid4())), ("POST", "/conversations"),
])
def test_all_manufacturing_routes_share_safe_internal_error_boundary(harness, monkeypatch, method, path):
    h = harness
    def broken(*a, **kw):
        raise ValueError(PRIVATE)
    if path.startswith("/work-orders"):
        monkeypatch.setattr(api.WorkOrderAccessService, "scope", broken)
    else:
        monkeypatch.setattr(api, "conversation_store", broken)
    with TestClient(h.c.app) as client:
        response = client.request(method, "/api/manufacturing" + path,
            **({"json": {"work_order_id": "WO-018"}} if method == "POST" else {}))
    assert response.status_code == 500 and response.json()["detail"]["error_code"] == "manufacturing_internal_error"
    assert response.headers["X-Request-ID"] == response.json()["detail"]["request_id"]
    assert PRIVATE not in response.text


def test_failed_followup_resolution_uses_explicit_input_error(harness):
    h = harness
    with TestClient(h.c.app) as client:
        conversation_id = create(client, h)
        assert ask(client, h, conversation_id=conversation_id).json()["answerable"]
        h.c.app.state.conversation_answer_service.resolver.resolve = lambda *a: ""
        response = ask(client, h, conversation_id=conversation_id, question="然后呢？")
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "manufacturing_input_invalid"


def test_storage_failure_while_auditing_preserves_primary_internal_error(harness, monkeypatch, caplog):
    def broken(_):
        raise ValueError(PRIVATE)
    def unavailable(*a, **kw):
        raise SQLAlchemyError(PRIVATE)
    harness.generation.hook = broken
    monkeypatch.setattr(api.AuditService, "record", unavailable)
    with TestClient(harness.c.app) as client:
        response = ask(client, harness)
    assert response.status_code == 500 and response.json()["detail"]["error_code"] == "manufacturing_internal_error"
    assert "failure_audit" in caplog.text and PRIVATE not in response.text + caplog.text
