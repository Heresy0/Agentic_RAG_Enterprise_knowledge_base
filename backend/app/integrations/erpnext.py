"""Read-only ERPNext Work Order projection into the existing source contract."""
from datetime import date, datetime
import json
from typing import Literal
from urllib.parse import quote, urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from pydantic import Field, SecretStr, ValidationError, model_validator

from ..manufacturing.contracts import Contract, Identifier, Text, WorkOrderRecord, canonical_digest
from .mes import MESError, MESNotFound, MESUnavailable


class ERPNextConfig(Contract):
    model_config = dict(extra='forbid', frozen=True, hide_input_in_errors=True)
    tenant_id: UUID
    source_system_id: Identifier
    base_url: str
    company_plants: dict[Text, Identifier] = Field(min_length=1, max_length=20)
    # This first integration is provisioned with this exact site timezone.
    site_timezone: Literal['Asia/Shanghai'] = 'Asia/Shanghai'
    api_key: SecretStr = Field(repr=False)
    api_secret: SecretStr = Field(repr=False)
    read_token: SecretStr = Field(repr=False)

    @model_validator(mode='after')
    def configured_endpoint(self):
        u = urlsplit(self.base_url)
        if (u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or
                u.path not in ('', '/') or u.query or u.fragment or '\\' in self.base_url or
                any(c.isspace() for c in self.base_url)):
            raise ValueError('Invalid configured ERPNext endpoint.')
        _ = u.port
        if u.scheme == 'http' and u.hostname not in ('frontend', '127.0.0.1', 'localhost', '::1'):
            raise ValueError('HTTP is limited to the isolated ERPNext frontend or loopback.')
        for secret, minimum in ((self.api_key, 10), (self.api_secret, 10), (self.read_token, 32)):
            value = secret.get_secret_value()
            if not minimum <= len(value) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in value):
                raise ValueError('Invalid ERPNext source credential.')
        ZoneInfo(self.site_timezone)
        return self


def source_timestamp(value, timezone, *, allow_date=False):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Missing source timestamp.')
    if len(value) == 10:
        parsed = date.fromisoformat(value)
        if not allow_date:
            raise ValueError('Source modification time must include time.')
        return parsed.isoformat(), 'date'
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed.isoformat(), 'datetime'


def project_work_order(raw, config, plants):
    """Use only actual_start_date; no plan/report/first-operation fallback."""
    if not isinstance(raw, dict):
        raise MESError()
    company = raw.get('company')
    plant = config.company_plants.get(company) if isinstance(company, str) else None
    if plant not in plants:
        raise MESNotFound()
    try:
        operations = raw['operations']
        if not isinstance(operations, list) or any(not isinstance(row, dict) for row in operations):
            raise ValueError('Invalid source operations.')
        operation = operations[0].get('operation') if len(operations) == 1 else None
        start = raw.get('actual_start_date')
        started, precision = source_timestamp(start, config.site_timezone, allow_date=True) if start not in (None, '') else (None, None)
        modified, _ = source_timestamp(raw['modified'], config.site_timezone)
        revision = canonical_digest(dict(name=raw['name'], company=company, production_item=raw.get('production_item'),
            operations=[dict(operation=row.get('operation')) for row in operations], actual_started_at=started,
            status=raw['status'], modified=modified, site_timezone=config.site_timezone, plant=plant))
        record = WorkOrderRecord(source_system_id=config.source_system_id, tenant_id=config.tenant_id,
            plant_code=plant, work_order_id=raw['name'], source_record_id=raw['name'],
            product_model=raw.get('production_item') or None, operation_code=operation or None,
            actual_started_at=started, actual_start_precision=precision, status=raw['status'],
            source_revision='erp-' + revision[:60], source_updated_at=modified)
    except (KeyError, TypeError, ValueError, ValidationError):
        raise MESError() from None
    for secret in (config.api_key, config.api_secret, config.read_token):
        if secret.get_secret_value() in record.model_dump_json():
            raise MESError()
    return record


class ERPNextClient:
    def __init__(self, config, *, transport=None):
        self.config = config
        self.client = httpx.AsyncClient(base_url=config.base_url.rstrip('/'),
            headers={'Authorization': 'token ' + config.api_key.get_secret_value() + ':' + config.api_secret.get_secret_value(),
                     'Accept': 'application/json', 'Accept-Encoding': 'identity'},
            timeout=httpx.Timeout(3, connect=2), follow_redirects=False, trust_env=False,
            transport=transport or httpx.AsyncHTTPTransport(retries=0))

    async def aclose(self):
        await self.client.aclose()

    async def _get(self, path, params=None):
        try:
            async with self.client.stream('GET', path, params=params) as response:
                if response.status_code in (403, 404):
                    raise MESNotFound()
                if response.status_code == 401 or response.status_code == 429 or response.status_code >= 500:
                    raise MESUnavailable()
                if response.status_code != 200 or 'application/json' not in response.headers.get('content-type', ''):
                    raise MESError()
                raw = bytearray()
                async for part in response.aiter_bytes():
                    if len(raw) + len(part) > 262144:
                        raise MESError()
                    raw.extend(part)
                payload = json.loads(raw)
                if not isinstance(payload, dict) or 'data' not in payload:
                    raise MESError()
                return payload['data']
        except httpx.TransportError:
            raise MESUnavailable() from None
        except (ValueError, RecursionError):
            raise MESError() from None

    async def read(self, work_order_id, plants):
        data = await self._get('/api/resource/Work%20Order/' + quote(work_order_id, safe=''))
        record = project_work_order(data, self.config, plants)
        if record.work_order_id != work_order_id:
            raise MESError()
        return record

    async def page(self, plants, *, after, limit):
        companies = sorted(company for company, plant in self.config.company_plants.items() if plant in plants)
        filters = [['company', 'in', companies], ['name', '>', after]]
        data = await self._get('/api/resource/Work%20Order', dict(fields=json.dumps(['name']), filters=json.dumps(filters),
            order_by='name asc', limit_page_length=limit + 1))
        if not isinstance(data, list) or len(data) > limit + 1:
            raise MESError()
        try:
            names = [row['name'] for row in data]
            if names != sorted(set(names)) or any(not isinstance(name, str) or name <= after for name in names):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise MESError() from None
        from pydantic import TypeAdapter
        try:
            names = TypeAdapter(list[Identifier]).validate_python(names)
        except ValidationError:
            raise MESError() from None
        records = [await self.read(name, plants) for name in names[:limit]]
        return records, names[limit - 1] if len(names) > limit else None
