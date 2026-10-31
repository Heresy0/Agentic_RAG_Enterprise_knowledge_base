import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from backend.app.manufacturing.contracts import WorkOrderFixtures
from backend.app.integrations.mes_contracts import MESConfigurationError
from services.mock_mes.app import SourceCredential, create_app
from services.mock_mes import main as source_main

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "m1-test-source-credential-not-production-0001"


def app_and_fixtures():
    fixtures = WorkOrderFixtures.model_validate_json((ROOT / "sample_docs/manufacturing_workorder_v1/work_orders.json").read_bytes())
    grant = SourceCredential(fixtures.tenant_id, fixtures.source_system_id,
        frozenset({"HC-P01", "HC-P02"}), SecretStr(TOKEN))
    return create_app(fixtures, (grant,)), fixtures


def headers(fixtures, plants="HC-P01"):
    return {"Authorization": "Bearer " + TOKEN, "X-MES-Tenant": str(fixtures.tenant_id), "X-MES-Plants": plants}


def test_mock_is_read_only_and_requires_credentials():
    async def scenario():
        app, fixtures = app_and_fixtures()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://mock-mes") as client:
            health = await client.get("/health")
            assert health.status_code == 200 and TOKEN not in health.text
            assert (await client.get("/v1/work-orders")).status_code == 401
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                assert (await client.request(method, "/v1/work-orders/WO-017/context", headers=headers(fixtures))).status_code == 405
            for path in ("/docs", "/openapi.json", "/v1/fixtures/reset"):
                assert (await client.get(path, headers=headers(fixtures))).status_code == 404
    asyncio.run(scenario())


def test_authorization_precedes_pagination_and_hidden_or_missing_records_match():
    async def scenario():
        app, fixtures = app_and_fixtures()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://mock-mes") as client:
            seen, cursor = [], None
            while True:
                response = await client.get("/v1/work-orders", params=dict(limit=2, **({"cursor":cursor} if cursor else {})), headers=headers(fixtures))
                assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
                payload = response.json(); assert "total" not in payload
                seen.extend(r["work_order_id"] for r in payload["items"])
                cursor = payload["next_cursor"]
                if cursor is None: break
            assert seen == ["WO-017", "WO-018", "WO-019", "WO-020", "WO-021", "WO-023", "WO-024"]
            hidden = await client.get("/v1/work-orders/WO-022/context", headers=headers(fixtures))
            missing = await client.get("/v1/work-orders/WO-999/context", headers=headers(fixtures))
            assert hidden.status_code == missing.status_code == 404 and hidden.json() == missing.json()
            raw = await client.get("/v1/work-orders/WO-020/context", headers=headers(fixtures))
            assert raw.json()["actual_started_at"] is None
            assert "reference_answer" not in raw.text and "target_business_revision" not in raw.text
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["tenant", "plant", "token"])
def test_source_validates_its_own_tenant_and_credential_bounds(change):
    async def scenario():
        app, fixtures = app_and_fixtures(); values = headers(fixtures)
        if change == "tenant": values["X-MES-Tenant"] = str(uuid4())
        elif change == "plant": values["X-MES-Plants"] = "HC-P99"
        else: values["Authorization"] = "Bearer " + "x" * 40
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://mock-mes") as client:
            response = await client.get("/v1/work-orders", headers=values)
            assert response.status_code == (401 if change == "token" else 404)
            assert TOKEN not in response.text and "WO-017" not in response.text
    asyncio.run(scenario())


def test_cursor_is_signed_and_bound_to_scope_and_page_size():
    async def scenario():
        app, fixtures = app_and_fixtures()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://mock-mes") as client:
            first = await client.get("/v1/work-orders", params={"limit":2}, headers=headers(fixtures))
            cursor = first.json()["next_cursor"]
            for candidate, limit, plants in ((cursor + "x", 2, "HC-P01"), (cursor, 3, "HC-P01"), (cursor, 2, "HC-P02")):
                response = await client.get("/v1/work-orders", params={"limit":limit,"cursor":candidate}, headers=headers(fixtures,plants))
                assert response.status_code == 422 and TOKEN not in response.text
    asyncio.run(scenario())


def test_source_startup_requires_explicit_matching_credentials():
    _, fixtures = app_and_fixtures()
    with pytest.raises(ValueError): create_app(fixtures, ())
    wrong = SourceCredential(uuid4(), fixtures.source_system_id, frozenset({"HC-P01"}), SecretStr(TOKEN))
    with pytest.raises(ValueError): create_app(fixtures, (wrong,))
    assert TOKEN not in repr(wrong)


def test_mistaken_fixture_path_does_not_disclose_local_file_contents(tmp_path,monkeypatch):
    path=tmp_path/"wrong-config.txt"
    path.write_text("PRIVATE_PASSWORD="+TOKEN,encoding="utf-8")
    monkeypatch.setenv("MOCK_MES_FIXTURES_FILE",str(path))
    with pytest.raises(MESConfigurationError) as captured: source_main.app()
    assert TOKEN not in str(captured.value) and "PRIVATE_PASSWORD" not in str(captured.value)
