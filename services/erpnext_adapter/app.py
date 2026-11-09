"""Authenticated compatibility endpoints over a fixed ERPNext source."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response

from backend.app.integrations.erpnext import ERPNextClient
from backend.app.integrations.mes import MESError
from backend.app.integrations.mes_contracts import WorkOrderPage
from backend.app.manufacturing.contracts import Identifier, canonical_digest


def create_app(config, *, transport=None):
    client = ERPNextClient(config, transport=transport)

    @asynccontextmanager
    async def lifespan(_):
        yield
        await client.aclose()

    app = FastAPI(title='ERPNext只读工单适配', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.erpnext_client = client

    def authorized(authorization: str | None = Header(None), tenant: str | None = Header(None, alias='X-MES-Tenant'),
                   plants: str | None = Header(None, alias='X-MES-Plants')):
        supplied = authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
        if not supplied.isascii() or len(supplied) > 512 or not hmac.compare_digest(supplied, config.read_token.get_secret_value()):
            raise HTTPException(401, '源调用身份无效。')
        try:
            requested = frozenset(plants.split(',')) if plants and len(plants) <= 6500 else frozenset()
            if UUID(tenant or '') != config.tenant_id or not requested or not requested <= set(config.company_plants.values()):
                raise ValueError()
        except ValueError:
            raise HTTPException(404, '业务范围不存在或不可访问。') from None
        return requested

    async def bounded(call):
        try:
            async with asyncio.timeout(4):
                return await call
        except TimeoutError:
            raise HTTPException(503, 'ERPNext来源暂不可用。') from None
        except MESError as exc:
            raise HTTPException(exc.status_code, exc.message) from None

    @app.get('/health')
    def health():
        return dict(status='ok', mode='erpnext_read_only')

    @app.get('/v1/work-orders', response_model=WorkOrderPage)
    async def page(response: Response, plants=Depends(authorized), limit: int = Query(20, ge=1, le=20),
                   cursor: str | None = Query(None, min_length=1, max_length=1024)):
        response.headers['Cache-Control'] = 'no-store'
        binding = canonical_digest(dict(tenant=str(config.tenant_id), source=config.source_system_id,
            endpoint=config.base_url, companies=config.company_plants, timezone=config.site_timezone, plants=sorted(plants), limit=limit))
        after = ''
        if cursor is not None:
            try:
                payload, signature = cursor.split('.')
                expected = hmac.new(config.read_token.get_secret_value().encode(), payload.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise ValueError()
                row = json.loads(base64.b64decode(payload + '=' * (-len(payload) % 4), altchars=b'-_', validate=True))
                if row['binding'] != binding or not isinstance(row['last'], str):
                    raise ValueError()
                after = row['last']
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise HTTPException(422, '分页游标无效或业务范围已变化。') from None
        records, last = await bounded(client.page(plants, after=after, limit=limit))
        next_cursor = None
        if last is not None:
            payload = base64.urlsafe_b64encode(json.dumps(dict(binding=binding, last=last), sort_keys=True).encode()).decode().rstrip('=')
            next_cursor = payload + '.' + hmac.new(config.read_token.get_secret_value().encode(), payload.encode(), hashlib.sha256).hexdigest()
        return WorkOrderPage(items=tuple(records), next_cursor=next_cursor)

    @app.get('/v1/work-orders/{work_order_id}/context')
    async def context(work_order_id: Identifier, response: Response, plants=Depends(authorized)):
        response.headers['Cache-Control'] = 'no-store'
        return await bounded(client.read(work_order_id, plants))

    return app
