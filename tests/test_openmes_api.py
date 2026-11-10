"""Real platform SQL authorization over the OpenMES adapter; no models."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from test_document_upload_api import api_context, _set_principal
from test_manufacturing_m3_api import harness
from test_openmes_adapter import Graph,config,TOKEN
from backend.app.db.business_models import BusinessScopeGrant
from backend.app.integrations.mes import MESConnector
from backend.app.integrations.mes_contracts import MESSourceConfig
from services.openmes_adapter.app import create_app


@pytest.fixture
def mes(harness):
    h=harness;g=Graph()
    app=create_app(config(tenant_id=h.c.tenant_id,source_system_id='mock-mes-hc'),transport=httpx.MockTransport(g))
    connector=MESConnector(MESSourceConfig(tenant_id=h.c.tenant_id,source_system_id='mock-mes-hc',
        base_url='http://openmes-adapter:8012',token=TOKEN,allowed_plants=('HC-P01',),batch_step_selection=True),
        transport=httpx.ASGITransport(app))
    h.c.app.state.mes_connectors={h.c.tenant_id:connector}
    yield h,g
    asyncio.run(connector.aclose());asyncio.run(app.state.openmes_client.aclose())


def test_read_pages_and_two_check_context_with_correct_entity_citations(mes):
    h,g=mes
    with TestClient(h.c.app) as c:
        assert c.get('/api/manufacturing/work-orders').json()['execution_kind']=='batch-step'
        batches=c.get('/api/manufacturing/work-orders/1/batches')
        assert batches.status_code==200 and batches.json()['items'][0]['batch_id']=='1'
        steps=c.get('/api/manufacturing/work-orders/1/batches/1/steps')
        assert steps.status_code==200 and len(steps.json()['items'])==2
        response=c.get('/api/manufacturing/work-orders/1/execution-context?batch_id=1&step_id=11')
        assert response.status_code==200,response.text
        body=response.json();assert body['source_checks']==2 and 'tenant_id' not in body['record']['work_order']
        refs=body['business_citations']
        assert {(x['source_entity'],x['source_record_id']) for x in refs}=={('Work Order','1'),('Batch','1'),('Batch Step','11')}
        assert next(x for x in refs if x['source_entity']=='Work Order' and x['field_name']=='actual_started_at')['value'] is None
        assert response.headers['cache-control']=='no-store'
        assert not h.generation.calls


def test_permission_denial_happens_before_upstream_and_cannot_leak_old_data(mes):
    h,g=mes;_set_principal(h.c,h.c.viewer)
    with TestClient(h.c.app) as c:
        for path in ('/batches','/batches/1/steps','/execution-context?batch_id=1&step_id=11'):
            r=c.get('/api/manufacturing/work-orders/1'+path)
            assert r.status_code==404 and 'HC-A17' not in r.text
    assert not g.calls


def test_revocation_during_read_discards_context(mes):
    h,g=mes
    def revoke(graph,request):
        if len(graph.calls)==3:
            with h.c.session_factory() as db:
                db.execute(delete(BusinessScopeGrant).where(BusinessScopeGrant.user_id==h.c.editor.user_id));db.commit()
    g.hook=revoke
    with TestClient(h.c.app) as c:
        r=c.get('/api/manufacturing/work-orders/1/execution-context?batch_id=1&step_id=11')
        assert r.status_code==404 and 'HC-A17' not in r.text


def test_changed_source_or_expected_revision_discards_context(mes):
    h,g=mes
    with TestClient(h.c.app) as c:
        first=c.get('/api/manufacturing/work-orders/1/execution-context?batch_id=1&step_id=11').json()
        original=first['record']['source_revision']
        g.batches['1']['steps'][0]['status']='DONE'
        assert c.get('/api/manufacturing/work-orders/1/execution-context?batch_id=1&step_id=11&expected_source_revision='+original).status_code==409
        def change(graph,request):
            if request.url.path=='/api/v1/batches/1':
                graph.batches['1']['steps'][0]['status']='PENDING' if graph.batches['1']['steps'][0]['status']=='DONE' else 'DONE'
        g.hook=change
        assert c.get('/api/manufacturing/work-orders/1/execution-context?batch_id=1&step_id=11').status_code==409


def test_foreign_selection_and_write_requests_are_rejected(mes):
    h,g=mes
    with TestClient(h.c.app) as c:
        assert c.get('/api/manufacturing/work-orders/1/execution-context?step_id=11').status_code==422
        assert c.get('/api/manufacturing/work-orders/1/execution-context?batch_id=2').status_code==404
        assert c.get('/api/manufacturing/work-orders/2/batches').status_code==404
        assert c.post('/api/manufacturing/work-orders/1/batches').status_code==405
        assert c.post('/api/manufacturing/qa',json=dict(question='查询批次',work_order_id='1',batch_id='1',operation_id='ROW-A')).status_code==422
    assert not h.generation.calls
