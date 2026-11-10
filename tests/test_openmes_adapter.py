"""OpenMES provenance, hierarchy, source changes and authorization boundaries."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.integrations.openmes import OpenMESClient, OpenMESConfig
from backend.app.integrations.mes import MESError, MESNotFound, MESContextChanged, MESConnector
from backend.app.integrations.mes_contracts import MESSourceConfig
from backend.app.manufacturing.batch_contracts import BatchExecutionRecord
from backend.app.manufacturing.batch_access import BatchExecutionAccess, public_execution
from services.openmes_adapter.app import create_app

TENANT=uuid4()
TOKEN='source-read-token-'+'r'*40
UPSTREAM='upstream-token-'+'s'*40
STAMP='2026-11-09T08:00:00.000000Z'


def config(**changes):
    return OpenMESConfig(**dict(dict(tenant_id=TENANT,source_system_id='openmes-hc',base_url='http://backend:8000',
        line_plants={'1':'HC-P01','2':'HC-P02'},upstream_tenant_id=1,api_token=UPSTREAM,read_token=TOKEN),**changes))


class Graph:
    def __init__(self):
        self.orders={str(i):dict(id=i,order_no='MES-WO-'+str(i),line_id=i,tenant_id=1,
            product_type={'code':'HC-A17'},status='IN_PROGRESS',updated_at=STAMP,
            planned_start_at='2026-10-01T08:00:00Z') for i in (1,2)}
        self.batches={str(i):dict(id=i,work_order_id=i,batch_number=1,lot_number='LOT-'+str(i),status='IN_PROGRESS',
            started_at='2026-10-02T08:10:00.000000Z',updated_at=STAMP,
            steps=[dict(id=i*10+j,batch_id=i,step_number=j,name='OP-'+str(j*10),status='IN_PROGRESS' if j==1 else 'PENDING',
                started_at='2026-10-02T08:20:00.000000Z' if j==1 else None,updated_at=STAMP) for j in (1,2)]) for i in (1,2)}
        self.calls=[];self.hook=None;self.status=None

    def __call__(self,request):
        self.calls.append(request)
        assert request.method=='GET' and request.headers['Authorization']=='Bearer '+UPSTREAM
        if self.hook:self.hook(self,request)
        if self.status:return httpx.Response(self.status,json={'message':'private failure'})
        path=request.url.path
        if path=='/api/v1/work-orders':value=list(self.orders.values())
        elif path.startswith('/api/v1/batches/'):value=self.batches.get(path.rsplit('/',1)[1])
        elif path.endswith('/batches'):
            wo=path.split('/')[-2];value=[x for x in self.batches.values() if str(x['work_order_id'])==wo]
        else:value=self.orders.get(path.rsplit('/',1)[1])
        return httpx.Response(200,json={'data':deepcopy(value)}) if value is not None else httpx.Response(404)


def headers(tenant=TENANT,plants='HC-P01'):
    return {'Authorization':'Bearer '+TOKEN,'X-MES-Tenant':str(tenant),'X-MES-Plants':plants}


def test_actual_times_are_separate_and_missing_order_time_is_not_inferred():
    async def run():
        g=Graph();c=OpenMESClient(config(),transport=httpx.MockTransport(g))
        try:
            result=await c.execution('1',{'HC-P01'},batch_id='1',step_id='11')
            assert result.work_order.actual_started_at is None
            assert result.work_order.operation_code is None
            assert result.batch.actual_started_at.endswith('08:10:00.000000Z')
            assert result.step.actual_started_at.endswith('08:20:00.000000Z')
            assert result.work_order_number=='MES-WO-1'
            unknown=await c.execution('1',{'HC-P01'},batch_id='1',step_id='12')
            assert unknown.step.actual_started_at is None and unknown.step.actual_start_precision is None
            assert BatchExecutionRecord.model_validate(result.model_dump(mode='json'))==result
        finally:await c.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('batch,step',[('2',None),('1','21'),('1','999'),('01',None),('1','011')])
def test_selection_must_belong_to_exact_order_batch_and_canonical_identity(batch,step):
    async def run():
        c=OpenMESClient(config(),transport=httpx.MockTransport(Graph()))
        try:
            with pytest.raises(MESNotFound):await c.execution('1',{'HC-P01'},batch_id=batch,step_id=step)
        finally:await c.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('field,value',[('line_id',2),('tenant_id',2),('tenant_id',True)])
def test_source_line_and_tenant_enforced(field,value):
    g=Graph();g.orders['1'][field]=value
    with TestClient(create_app(config(),transport=httpx.MockTransport(g))) as c:
        assert c.get('/v1/work-orders/1/context',headers=headers()).status_code==404


@pytest.mark.parametrize('field,value',[('started_at','2026-10-02 08:00:00'),('updated_at',123),('status',UPSTREAM)])
def test_invalid_time_or_credential_reflection_not_delivered(field,value):
    g=Graph();g.batches['1'][field]=value
    with TestClient(create_app(config(),transport=httpx.MockTransport(g))) as c:
        response=c.get('/v1/work-orders/1/execution-context?batch_id=1',headers=headers())
        assert response.status_code==502 and UPSTREAM not in response.text


def test_source_auth_and_read_only_routes_do_not_reach_upstream():
    g=Graph()
    with TestClient(create_app(config(),transport=httpx.MockTransport(g))) as c:
        for h in ({},headers(tenant=uuid4()),headers(plants='HC-P03')):
            assert c.get('/v1/work-orders',headers=h).status_code in (401,403)
        assert c.post('/v1/work-orders/1/context',headers=headers()).status_code==405
        assert c.get('/v1/work-orders/1/execution-context?step_id=11',headers=headers()).status_code==422
    assert not g.calls


def test_source_changes_cursor_scope_and_pending_unavailability():
    g=Graph()
    with TestClient(create_app(config(),transport=httpx.MockTransport(g))) as c:
        page=c.get('/v1/work-orders?limit=1',headers=headers(plants='HC-P01,HC-P02')).json()
        cursor=page['next_cursor'];assert cursor
        assert c.get('/v1/work-orders?limit=1&cursor='+cursor,headers=headers()).status_code==422
        g.orders['1']['status']='PAUSED'
        assert c.get('/v1/work-orders?limit=1&cursor='+cursor,headers=headers(plants='HC-P01,HC-P02')).status_code==409
        g.status=500
        assert c.get('/v1/work-orders/1/context',headers=headers()).status_code==503


def test_parent_changes_during_selected_child_fetch_are_rejected():
    g=Graph()
    def change(graph,request):
        if request.url.path.startswith('/api/v1/batches/'):
            graph.orders['1']['status']='PAUSED'
    g.hook=change
    with TestClient(create_app(config(),transport=httpx.MockTransport(g))) as c:
        assert c.get('/v1/work-orders/1/execution-context?batch_id=1',headers=headers()).status_code==409


def test_execution_revision_is_stable_and_only_selected_child_matters():
    async def run():
        g=Graph();c=OpenMESClient(config(),transport=httpx.MockTransport(g))
        try:
            first=await c.execution('1',{'HC-P01'},batch_id='1',step_id='11')
            g.batches['2']['steps'][0]['status']='DONE'
            g.batches['1']['steps'][1]['status']='READY'
            assert (await c.execution('1',{'HC-P01'},batch_id='1',step_id='11'))==first
            g.batches['1']['steps'][0]['status']='DONE'
            assert (await c.execution('1',{'HC-P01'},batch_id='1',step_id='11')).source_revision!=first.source_revision
            raw=first.model_dump(mode='json');raw['step']['status']='FORGED'
            with pytest.raises(ValidationError):BatchExecutionRecord.model_validate(raw)
        finally:await c.aclose()
    asyncio.run(run())


def test_hierarchy_capability_flags_cannot_conflict():
    with pytest.raises(ValidationError):
        MESSourceConfig(tenant_id=TENANT,source_system_id='openmes-hc',base_url='http://openmes-adapter:8012',
            token=TOKEN,allowed_plants=('HC-P01',),execution_selection=True,batch_step_selection=True)


def test_source_url_cannot_be_request_controlled():
    for url in ('http://example.com','http://backend@evil','http://backend/path','http://backend?token=x'):
        with pytest.raises(ValidationError):config(base_url=url)
