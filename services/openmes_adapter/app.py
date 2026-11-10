"""Fixed GET routes with source authentication and plant-scoped projections."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from backend.app.integrations.openmes import OpenMESClient
from backend.app.integrations.mes import MESError, MESContextChanged, MESInvalidCursor
from backend.app.integrations.mes_contracts import WorkOrderPage
from backend.app.manufacturing.batch_contracts import BatchPage, StepPage
from backend.app.manufacturing.contracts import Identifier, canonical_digest


def create_app(config,*,transport=None):
    client=OpenMESClient(config,transport=transport)
    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()
    app=FastAPI(title='OpenMES read-only adapter',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.openmes_client=client

    @app.exception_handler(MESError)
    async def failed(request,exc):
        return JSONResponse(status_code=exc.status_code,content={'detail':{'error_code':exc.error_code,'message':exc.message}},headers={'Cache-Control':'no-store'})

    async def authorized(authorization: str=Header(''),x_mes_tenant: str=Header(''),x_mes_plants: str=Header('')):
        if not authorization.isascii() or len(authorization)>520 or not hmac.compare_digest(authorization,'Bearer '+config.read_token.get_secret_value()):
            raise HTTPException(401,'Source authentication failed.')
        try:tenant=UUID(x_mes_tenant)
        except ValueError:raise HTTPException(403,'Source scope denied.') from None
        plants=frozenset(x_mes_plants.split(','))
        if tenant!=config.tenant_id or not plants or not plants<=set(config.line_plants.values()):
            raise HTTPException(403,'Source scope denied.')
        return plants

    async def bounded(coro):
        try:
            async with asyncio.timeout(4.5):return await coro
        except TimeoutError:
            from backend.app.integrations.mes import MESUnavailable
            raise MESUnavailable() from None

    def cursor_value(value,*,plants,limit,inventory):
        if value is None:return 0
        try:
            raw,signature=value.split('.')
            expected=hmac.new(config.read_token.get_secret_value().encode(),raw.encode(),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected,signature):raise ValueError('signature')
            data=json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
            if (data['plants'],data['limit'],data['configuration'])!=(sorted(plants),limit,client.configuration_sha256):raise ValueError('binding')
            if data['inventory']!=inventory:raise MESContextChanged()
            if type(data['after']) is not int or data['after']<1:raise ValueError('position')
            return data['after']
        except (ValueError,KeyError,TypeError):raise MESInvalidCursor() from None

    @app.get('/v1/source')
    async def source(response: Response,plants=Depends(authorized)):
        response.headers['Cache-Control']='no-store'
        from backend.app.manufacturing.source_registration import OpenMESSourceDescriptor
        return OpenMESSourceDescriptor(tenant_id=config.tenant_id,
            source_system_id=config.source_system_id,configuration_sha256=client.configuration_sha256)

    @app.get('/v1/work-orders')
    async def orders(response: Response,plants=Depends(authorized),limit: int=Query(20,ge=1,le=20),
                     cursor: str|None=Query(None,min_length=1,max_length=1024)):
        response.headers['Cache-Control']='no-store'
        rows=await bounded(client.orders(plants));inventory=canonical_digest([x.model_dump(mode='json') for x in rows])
        after=cursor_value(cursor,plants=plants,limit=limit,inventory=inventory)
        remaining=[x for x in rows if int(x.work_order_id)>after];page=remaining[:limit];following=None
        if len(remaining)>limit:
            raw=base64.urlsafe_b64encode(json.dumps(dict(after=int(page[-1].work_order_id),plants=sorted(plants),limit=limit,
                inventory=inventory,configuration=client.configuration_sha256),separators=(',',':')).encode()).decode().rstrip('=')
            following=raw+'.'+hmac.new(config.read_token.get_secret_value().encode(),raw.encode(),hashlib.sha256).hexdigest()
        return WorkOrderPage(items=tuple(page),next_cursor=following)

    @app.get('/v1/work-orders/{work_order_id}/context')
    async def context(work_order_id: Identifier,response: Response,plants=Depends(authorized)):
        response.headers['Cache-Control']='no-store'
        return await bounded(client.order(work_order_id,plants))

    @app.get('/v1/work-orders/{work_order_id}/batches')
    async def batches(work_order_id: Identifier,response: Response,plants=Depends(authorized)):
        response.headers['Cache-Control']='no-store'
        order,rows=await bounded(client.batches(work_order_id,plants))
        revision='mes-'+canonical_digest(dict(order=order.model_dump(mode='json'),batches=[x.model_dump(mode='json') for x in rows]))[:60]
        return BatchPage(work_order_id=work_order_id,source_revision=revision,items=tuple(rows))

    @app.get('/v1/work-orders/{work_order_id}/batches/{batch_id}/steps')
    async def steps(work_order_id: Identifier,batch_id: Identifier,response: Response,plants=Depends(authorized)):
        response.headers['Cache-Control']='no-store'
        async def read():
            order=await client.order(work_order_id,plants)
            batch,rows=await client.selected_batch(order,batch_id)
            if (await client.order(work_order_id,plants))!=order:raise MESContextChanged()
            return batch,rows,order
        batch,rows,order=await bounded(read())
        revision='mes-'+canonical_digest(dict(order=order.model_dump(mode='json'),batch=batch.model_dump(mode='json'),steps=[x.model_dump(mode='json') for x in rows]))[:60]
        return StepPage(work_order_id=work_order_id,batch_id=batch_id,source_revision=revision,items=tuple(rows))

    @app.get('/v1/work-orders/{work_order_id}/execution-context')
    async def execution(work_order_id: Identifier,response: Response,plants=Depends(authorized),
                        batch_id: Identifier|None=None,step_id: Identifier|None=None):
        response.headers['Cache-Control']='no-store'
        if step_id is not None and batch_id is None:raise HTTPException(422,'选择步骤时必须指定批次。')
        return await bounded(client.execution(work_order_id,plants,batch_id=batch_id,step_id=step_id))

    @app.get('/v1/work-orders/{work_order_id}/qa-context')
    async def qa_context(work_order_id: Identifier,response: Response,plants=Depends(authorized),
                         batch_id: Identifier|None=None,step_id: Identifier|None=None):
        from backend.app.manufacturing.batch_qa_contracts import qa_record
        response.headers['Cache-Control']='no-store'
        if step_id is not None and batch_id is None:raise HTTPException(422,'选择步骤时必须指定批次。')
        return qa_record(await bounded(client.execution(work_order_id,plants,batch_id=batch_id,step_id=step_id)))

    return app
