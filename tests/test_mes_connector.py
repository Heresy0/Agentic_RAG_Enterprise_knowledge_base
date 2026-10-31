import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from backend.app.integrations.mes import (MESConnector, MESError, MESUnavailable, MESNotFound,
                                        MESContextChanged, MESInvalidCursor)
from backend.app.integrations.mes_contracts import MESConfigurationError, MESSourceConfig, load_source_configs
from backend.app.manufacturing.contracts import WorkOrderFixtures
from backend.app.security.business_scope import BusinessAuthorizationDenied, BusinessScope
from services.mock_mes.app import SourceCredential, create_app

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "m1-test-connector-credential-not-production-0002"


def fixtures():
    return WorkOrderFixtures.model_validate_json((ROOT / "sample_docs/manufacturing_workorder_v1/work_orders.json").read_bytes())


def config(**changes):
    data = fixtures()
    return MESSourceConfig(source_system_id=data.source_system_id, tenant_id=data.tenant_id,
        base_url="http://mock-mes", allowed_plants=("HC-P01", "HC-P02"), token=SecretStr(TOKEN), **changes)


def scope(**changes):
    values = dict(tenant_id=fixtures().tenant_id, user_id=uuid4(), source_system_id="mock-mes-hc", plant_codes=frozenset({"HC-P01"}))
    return BusinessScope(**(values | changes))


def source_app():
    data = fixtures()
    return create_app(data, (SourceCredential(data.tenant_id, data.source_system_id,
        frozenset({"HC-P01", "HC-P02"}), SecretStr(TOKEN)),))


def test_connector_roundtrip_preserves_unknowns_precision_and_scope():
    async def scenario():
        async with MESConnector(config(), transport=httpx.ASGITransport(source_app())) as client:
            access = scope()
            old = await client.read_context("WO-018", scope=access)
            assert old.record.actual_started_at == "2026-09-28" and old.record.actual_start_precision == "date"
            assert old.fetched_at.tzinfo and old.payload_sha256 == old.record.payload_sha256
            checked = await client.verify_context(old, scope=access)
            assert checked.fetched_at == old.fetched_at and checked.last_verified_at >= old.last_verified_at
            unknown = await client.read_context("WO-020", scope=access)
            assert unknown.record.actual_started_at is None
            page = await client.list_work_orders(scope=access, limit=2)
            assert len(page.items) == 2 and page.next_cursor
            second = await client.list_work_orders(scope=access, limit=2, cursor=page.next_cursor)
            assert second.items[0].work_order_id == "WO-019"
            with pytest.raises(MESNotFound): await client.read_context("WO-022", scope=access)
            with pytest.raises(MESContextChanged): await client.read_context("WO-017", scope=access, expected_source_revision="r0")
            with pytest.raises(MESInvalidCursor): await client.list_work_orders(scope=access, cursor="forged")
    asyncio.run(scenario())


@pytest.mark.parametrize("status,error", [(404,MESNotFound),(401,MESUnavailable),(403,MESUnavailable),
    (429,MESUnavailable),(500,MESUnavailable),(503,MESUnavailable),(302,MESError),(206,MESError),(204,MESError)])
def test_http_errors_are_bounded_redacted_and_not_retried(status, error):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="sensitive source body " + TOKEN,
                              headers={"Location":"https://example.invalid/steal"})
    async def scenario():
        async with MESConnector(config(), transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(error) as captured: await client.read_context("WO-017", scope=scope())
            assert TOKEN not in str(captured.value) and "sensitive" not in str(captured.value)
    asyncio.run(scenario())
    assert len(calls) == 1 and calls[0].url.host == "mock-mes"


@pytest.mark.parametrize("change", ["tenant","source","plant","work_order","extra","bad_time","oversize_header",
                                   "wrong_length","compressed","bad_json","html","null"])
def test_source_response_must_be_complete_typed_and_authorized(change):
    record = fixtures().records[0].model_dump(mode="json")
    headers = {"content-type":"application/json"}
    if change == "tenant": record["tenant_id"] = str(uuid4())
    elif change == "source": record["source_system_id"] = "wrong-source"
    elif change == "plant": record["plant_code"] = "HC-P02"
    elif change == "work_order": record["work_order_id"] = "WO-018"
    elif change == "extra": record["service_token"] = TOKEN
    elif change == "bad_time": record["actual_started_at"] = "2026-10-26T08:10:00"
    elif change == "oversize_header": headers["content-length"] = "65537"
    elif change == "wrong_length": headers["content-length"] = "1"
    elif change == "compressed": headers["content-encoding"] = "gzip"
    elif change == "html": headers["content-type"] = "text/html"
    raw = b"not-json" if change == "bad_json" else b"null" if change == "null" else json.dumps(record).encode()
    async def scenario():
        # A raw stream keeps content-encoding validation ahead of decompression.
        async def chunks(): yield raw
        def handler(request): return httpx.Response(200, headers=headers, content=chunks())
        async with MESConnector(config(), transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(MESError): await client.read_context("WO-017", scope=scope())
    asyncio.run(scenario())


def test_revision_change_and_same_revision_payload_corruption_are_distinct():
    async def scenario(change_revision):
        record = fixtures().records[0].model_dump(mode="json"); calls=[]
        def handler(request):
            calls.append(request)
            payload = dict(record)
            if len(calls)>1:
                payload["status"] = "已变更"
                if change_revision: payload["source_revision"] = "r2"
            return httpx.Response(200, json=payload)
        async with MESConnector(config(), transport=httpx.MockTransport(handler)) as client:
            access=scope(); previous=await client.read_context("WO-017", scope=access)
            with pytest.raises(MESContextChanged if change_revision else MESError) as captured:
                await client.verify_context(previous, scope=access)
            assert captured.value.status_code == (409 if change_revision else 502)
    asyncio.run(scenario(True)); asyncio.run(scenario(False))


def test_unconfigured_scope_is_rejected_before_any_http_request():
    calls=[]
    def handler(request): calls.append(request); return httpx.Response(500)
    async def scenario():
        async with MESConnector(config(), transport=httpx.MockTransport(handler)) as client:
            for access in (scope(tenant_id=uuid4()), scope(source_system_id="wrong"), scope(plant_codes=frozenset())):
                with pytest.raises(BusinessAuthorizationDenied): await client.read_context("WO-017", scope=access)
    asyncio.run(scenario()); assert calls == []


class TrickleStream(httpx.AsyncByteStream):
    def __init__(self, *, delay=0, count=20, size=4096):
        self.delay, self.count, self.size, self.closed = delay, count, size, False
    async def __aiter__(self):
        for _ in range(self.count):
            await asyncio.sleep(self.delay)
            yield b" " * self.size
    async def aclose(self): self.closed = True


@pytest.mark.parametrize("kind", ["deadline", "size"])
def test_streaming_body_has_a_total_deadline_and_size_limit(kind):
    stream = TrickleStream(delay=.02 if kind=="deadline" else 0)
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type":"application/json"}, stream=stream)
    async def scenario():
        async with MESConnector(config(call_timeout=.05 if kind=="deadline" else 5),
                                transport=httpx.MockTransport(handler)) as client:
            start=time.monotonic()
            with pytest.raises(MESUnavailable if kind=="deadline" else MESError):
                await client.read_context("WO-017", scope=scope())
            if kind=="deadline": assert time.monotonic()-start < 1
    asyncio.run(scenario())
    assert len(calls)==1 and stream.closed


def test_transport_failure_is_not_retried_or_exposed():
    calls=[]
    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("secret=" + TOKEN, request=request)
    async def scenario():
        async with MESConnector(config(), transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(MESUnavailable) as captured: await client.read_context("WO-017", scope=scope())
            assert TOKEN not in str(captured.value)
    asyncio.run(scenario()); assert len(calls)==1


@pytest.mark.parametrize("kind", ["json_key", "record_identity"])
def test_duplicate_source_identity_is_rejected(kind):
    record=fixtures().records[0].model_dump(mode="json")
    duplicate=dict(record,work_order_id="WO-018")
    raw=b'{"tenant_id":"ignored",'+json.dumps(record).encode()[1:]
    def handler(request):
        return (httpx.Response(200,content=raw,headers={"content-type":"application/json"}) if kind=="json_key"
                else httpx.Response(200,json={"items":[record,duplicate],"next_cursor":None}))
    async def scenario():
        async with MESConnector(config(),transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(MESError):
                if kind=="json_key": await client.read_context("WO-017",scope=scope())
                else: await client.list_work_orders(scope=scope())
    asyncio.run(scenario())


@pytest.mark.parametrize("url", ["http://example.com", "https://user:secret@example.com", "https://example.com/?url=bad",
                               "https://example.com/#bad", "file:///tmp/source", "http://mock-mes/api"])
def test_source_url_is_server_configured_and_cannot_embed_credentials(url):
    with pytest.raises(ValidationError) as captured:
        MESSourceConfig.model_validate(config().model_dump() | {"base_url":url})
    assert TOKEN not in str(captured.value)


def test_source_loader_resolves_only_explicit_env_credentials_and_redacts_failures(tmp_path):
    path=tmp_path/"sources.json"
    source=json.loads((ROOT/"config/manufacturing_sources.example.json").read_bytes())
    path.write_text(json.dumps(source),encoding="utf-8")
    configs=load_source_configs(path,{"MOCK_MES_READ_TOKEN":TOKEN})
    assert TOKEN not in repr(configs)
    with pytest.raises(MESConfigurationError): load_source_configs(path,{})
    source["sources"][0]["base_url"]="https://user:secret@example.com"
    path.write_text(json.dumps(source),encoding="utf-8")
    with pytest.raises(MESConfigurationError) as captured: load_source_configs(path,{"MOCK_MES_READ_TOKEN":TOKEN})
    assert TOKEN not in str(captured.value) and "secret" not in str(captured.value)
