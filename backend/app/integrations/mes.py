"""Bounded asynchronous MES reads. No redirects, retries, model tools or caches."""
import asyncio
from datetime import datetime, timezone
import json
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter, ValidationError

from ..manufacturing.contracts import Identifier, WorkOrderContext, WorkOrderRecord
from ..security.business_scope import BusinessAuthorizationDenied, BusinessScope
from .mes_contracts import MESSourceConfig, WorkOrderPage


class MESError(RuntimeError):
    status_code = 502
    error_code = "mes_invalid_response"
    message = "业务来源返回的数据无法验证。"

    def __init__(self):
        super().__init__(self.message)


class MESUnavailable(MESError):
    status_code = 503
    error_code = "mes_unavailable"
    message = "业务来源暂不可用，请稍后重新查询。"


class MESNotFound(MESError):
    status_code = 404
    error_code = "work_order_unavailable"
    message = "工单不存在或不可访问。"


class MESContextChanged(MESError):
    status_code = 409
    error_code = "mes_context_changed"
    message = "工单记录已变化，请重新查询并开始新对话。"


class MESInvalidCursor(MESError):
    status_code = 422
    error_code = "mes_cursor_invalid"
    message = "分页游标无效或业务范围已变化，请重新查询。"


def _reject_constant(_):
    raise ValueError("non-finite JSON number")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate source JSON field")
        result[key] = value
    return result


class MESConnector:
    def __init__(self, config: MESSourceConfig, *, transport=None):
        self.config = config
        self.client = httpx.AsyncClient(transport=transport or httpx.AsyncHTTPTransport(retries=0),
            follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(config.call_timeout, connect=min(config.connect_timeout, config.call_timeout)),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self):
        await self.client.aclose()

    def _headers(self, scope: BusinessScope):
        if (scope.tenant_id != self.config.tenant_id or scope.source_system_id != self.config.source_system_id
                or not scope.plant_codes):
            raise BusinessAuthorizationDenied()
        plants = scope.plant_codes & frozenset(self.config.allowed_plants)
        if not plants:
            raise BusinessAuthorizationDenied()
        return dict(Authorization="Bearer " + self.config.token.get_secret_value(),
            **{"X-MES-Tenant": str(scope.tenant_id), "X-MES-Plants": ",".join(sorted(plants)),
               "Accept": "application/json", "Accept-Encoding": "identity"}), plants

    async def _get(self, path, *, scope: BusinessScope, params=None):
        headers, plants = self._headers(scope)
        url = self.config.base_url.rstrip("/") + path
        try:
            async with asyncio.timeout(self.config.call_timeout):
                async with self.client.stream("GET", url, headers=headers, params=params) as response:
                    if response.status_code == 404:
                        raise MESNotFound()
                    if response.status_code in (401, 403, 429) or response.status_code >= 500:
                        raise MESUnavailable()
                    if response.status_code == 422 and path == "/v1/work-orders" and params and params.get("cursor"):
                        raise MESInvalidCursor()
                    if response.status_code != 200:
                        raise MESError()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type != "application/json" and not content_type.endswith("+json"):
                        raise MESError()
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise MESError()
                    declared = response.headers.get("content-length")
                    if declared is not None and (not declared.isdecimal() or int(declared) > self.config.max_response_bytes):
                        raise MESError()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > self.config.max_response_bytes:
                            raise MESError()
                        raw.extend(chunk)
                    if declared is not None and len(raw) != int(declared):
                        raise MESError()
                    payload = json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
                    return payload, plants
        except (TimeoutError, httpx.TimeoutException):
            raise MESUnavailable() from None
        except (httpx.RemoteProtocolError, httpx.DecodingError, ValueError, RecursionError):
            raise MESError() from None
        except httpx.TransportError:
            raise MESUnavailable() from None

    def _validate_record(self, raw, scope, plants):
        try:
            record = WorkOrderRecord.model_validate(raw)
        except ValidationError:
            raise MESError() from None
        if not scope.contains_record(record) or record.plant_code not in plants:
            raise MESError()
        if len(record.model_dump_json(ensure_ascii=False)) > self.config.max_context_characters:
            raise MESError()
        return record

    async def read_context(self, work_order_id: str, *, scope: BusinessScope,
                           expected_source_revision=None) -> WorkOrderContext:
        TypeAdapter(Identifier).validate_python(work_order_id)
        if expected_source_revision is not None:
            TypeAdapter(Identifier).validate_python(expected_source_revision)
        payload, plants = await self._get(f"/v1/work-orders/{quote(work_order_id, safe='')}/context", scope=scope)
        record = self._validate_record(payload, scope, plants)
        if record.work_order_id != work_order_id:
            raise MESError()
        if expected_source_revision is not None and record.source_revision != expected_source_revision:
            raise MESContextChanged()
        now = datetime.now(timezone.utc)
        return WorkOrderContext(record=record, fetched_at=now, last_verified_at=now,
                                payload_sha256=record.payload_sha256)

    async def verify_context(self, previous: WorkOrderContext, *, scope: BusinessScope) -> WorkOrderContext:
        if not scope.contains_record(previous.record):
            raise BusinessAuthorizationDenied()
        current = await self.read_context(previous.record.work_order_id, scope=scope,
                                           expected_source_revision=previous.record.source_revision)
        if current.payload_sha256 != previous.payload_sha256:
            raise MESError()
        return WorkOrderContext(record=current.record, fetched_at=previous.fetched_at,
            last_verified_at=current.last_verified_at, payload_sha256=current.payload_sha256)

    async def list_work_orders(self, *, scope: BusinessScope, cursor=None, limit=20) -> WorkOrderPage:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("page limit must be between 1 and 20")
        if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024):
            raise ValueError("invalid cursor")
        params = dict(limit=limit)
        if cursor is not None:
            params["cursor"] = cursor
        payload, plants = await self._get("/v1/work-orders", scope=scope, params=params)
        try:
            page = WorkOrderPage.model_validate(payload)
        except ValidationError:
            raise MESError() from None
        if len(page.items) > limit:
            raise MESError()
        for record in page.items:
            self._validate_record(record.model_dump(mode="json"), scope, plants)
        return page
