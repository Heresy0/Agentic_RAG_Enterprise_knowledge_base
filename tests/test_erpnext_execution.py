"""Selection identity, parent/child provenance and legacy contract compatibility."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from test_erpnext_adapter import config, order, scope, TOKEN, SECRET, TENANT
from test_manufacturing_m3_api import harness
from test_document_upload_api import api_context
from backend.app.integrations.mes import MESConnector, MESContextChanged, MESInvalidCursor, MESNotFound, MESError
from backend.app.integrations.mes_contracts import MESSourceConfig
from backend.app.manufacturing.execution_contracts import (ExecutionWorkOrderRecord, ExecutionWorkOrderContext,
    ManufacturingExecutionQuestion, context_from_payload)
from backend.app.manufacturing.contracts import WorkOrderRecord, ManufacturingQuestion
from services.erpnext_adapter.app import create_app


class ERPGraph:
    def __init__(self, work_order='MFG-WO-2026-00001'):
        self.order = order(name=work_order, actual_start_date='2026-09-28 08:10:00', operations=[
            dict(name='ROW-A', operation='OP-20', status='Completed', actual_start_time='2026-09-28 08:10:00'),
            dict(name='ROW-B', operation='OP-20', status='Pending', actual_start_time=None)])
        self.cards = {}
        for name, operation, start in [('JC-A','ROW-A','2026-10-26 09:30:00'), ('JC-A2','ROW-A',None), ('JC-B','ROW-B',None)]:
            self.cards[name] = dict(name=name, work_order=work_order, operation_id=operation, operation='OP-20',
                production_item='HC-A17', company='HC Lab', status='Work In Progress' if start else 'Open',
                actual_start_date=start, modified='2026-11-09 08:11:00', docstatus=0)
        self.calls = []
        self.hook = None

    def __call__(self, request):
        self.calls.append(request)
        if self.hook:
            self.hook(self, request)
        assert request.method == 'GET'
        if request.url.path == '/api/resource/Work Order/' + self.order['name']:
            return httpx.Response(200, json={'data': deepcopy(self.order)})
        if request.url.path == '/api/resource/Job Card':
            filters = json.loads(request.url.params['filters'])
            assert filters[:3] == [['work_order','=',self.order['name']], ['operation_id','=',filters[1][2]], ['docstatus','<',2]]
            values = [row for row in self.cards.values() if row['operation_id'] == filters[1][2] and row['name'] > filters[3][2] and row['docstatus'] < 2]
            return httpx.Response(200, json={'data': deepcopy(values[:int(request.url.params['limit_page_length'])])})
        if request.url.path.startswith('/api/resource/Job Card/'):
            row = self.cards.get(request.url.path.rsplit('/',1)[1])
            return httpx.Response(200, json={'data': deepcopy(row)}) if row else httpx.Response(404)
        return httpx.Response(404)


def connector_for_graph(graph, *, tenant=TENANT, source='erpnext-hc'):
    cfg = config().model_copy(update=dict(tenant_id=tenant, source_system_id=source))
    app = create_app(cfg, transport=httpx.MockTransport(graph))
    connector = MESConnector(MESSourceConfig(tenant_id=tenant, source_system_id=source, base_url='http://erpnext-adapter',
        allowed_plants=('HC-P01','HC-P02'), token=TOKEN, execution_selection=True), transport=httpx.ASGITransport(app))
    return connector, app


def run_graph(graph, scenario):
    async def run():
        connector, app = connector_for_graph(graph)
        try:
            async with connector:
                await scenario(connector)
        finally:
            await app.state.erpnext_client.aclose()
    asyncio.run(run())


def test_choices_bind_child_row_identity_and_preserve_three_start_scopes():
    graph = ERPGraph()
    async def scenario(connector):
        operations = await connector.list_operations(graph.order['name'], scope=scope())
        assert [row.operation_id for row in operations.items] == ['ROW-A','ROW-B']
        assert all(row.operation_code == 'OP-20' for row in operations.items)
        first = await connector.list_job_cards(graph.order['name'], 'ROW-A', scope=scope(), limit=1)
        assert first.items[0].job_card_id == 'JC-A' and first.next_cursor
        second = await connector.list_job_cards(graph.order['name'], 'ROW-A', scope=scope(), limit=1, cursor=first.next_cursor)
        assert second.items[0].job_card_id == 'JC-A2' and second.items[0].actual_started_at is None
        context = await connector.read_context(graph.order['name'], scope=scope(), operation_id='ROW-A', job_card_id='JC-A')
        assert isinstance(context, ExecutionWorkOrderContext) and isinstance(context.record, ExecutionWorkOrderRecord)
        assert context.record.actual_started_at == '2026-09-28T08:10:00+08:00'
        assert context.record.execution.operation_actual_started_at == '2026-09-28T08:10:00+08:00'
        assert context.record.execution.job_card_actual_started_at == '2026-10-26T09:30:00+08:00'
        assert context_from_payload(context.model_dump(mode='json')) == context
        checked = await connector.verify_context(context, scope=scope())
        assert checked.record == context.record and checked.fetched_at == context.fetched_at
        unknown = await connector.read_context(graph.order['name'], scope=scope(), operation_id='ROW-B', job_card_id='JC-B')
        assert unknown.record.execution.operation_actual_started_at is None and unknown.record.execution.job_card_actual_started_at is None
    run_graph(graph, scenario)


@pytest.mark.parametrize('operation,card', [('ROW-A','JC-B'), ('ROW-B','JC-A'), ('OP-20','JC-A'), ('ROW-A','unknown')])
def test_mismatched_or_ambiguous_selection_cannot_resolve_by_operation_code(operation, card):
    async def scenario(connector):
        with pytest.raises(MESNotFound):
            await connector.read_context('MFG-WO-2026-00001', scope=scope(), operation_id=operation, job_card_id=card)
    run_graph(ERPGraph(), scenario)


@pytest.mark.parametrize('change', [dict(work_order='OTHER-WO'), dict(operation_id='ROW-B'), dict(operation='OP-30'),
    dict(company='Other Lab'), dict(production_item='HC-A18'), dict(docstatus=2), dict(docstatus=True)])
def test_selected_card_must_belong_to_exact_authorized_parent(change):
    graph = ERPGraph(); graph.cards['JC-A'].update(change)
    async def scenario(connector):
        with pytest.raises(MESNotFound):
            await connector.read_context(graph.order['name'], scope=scope(), operation_id='ROW-A', job_card_id='JC-A')
    run_graph(graph, scenario)


def test_parent_change_during_child_read_and_later_card_changes_stop_delivery():
    graph = ERPGraph()
    async def scenario(connector):
        original = await connector.read_context(graph.order['name'], scope=scope(), operation_id='ROW-A', job_card_id='JC-A')
        graph.cards['JC-A']['actual_start_date'] = '2026-10-27 09:30:00'
        with pytest.raises(MESContextChanged):
            await connector.verify_context(original, scope=scope())
        def change_parent(state, request):
            if request.url.path == '/api/resource/Job Card/JC-A':
                state.order['modified'] = '2026-11-09 10:00:00'
        graph.hook = change_parent
        with pytest.raises(MESContextChanged):
            await connector.read_context(graph.order['name'], scope=scope(), operation_id='ROW-A', job_card_id='JC-A')
    run_graph(graph, scenario)


def test_job_card_cursor_binds_parent_operation_scope_and_source_revision():
    graph = ERPGraph()
    async def scenario(connector):
        first = await connector.list_job_cards(graph.order['name'], 'ROW-A', scope=scope(), limit=1)
        for operation, cursor, limit in [('ROW-B',first.next_cursor,1),('ROW-A',first.next_cursor+'x',1),('ROW-A',first.next_cursor,2)]:
            with pytest.raises(MESInvalidCursor):
                await connector.list_job_cards(graph.order['name'], operation, scope=scope(), cursor=cursor, limit=limit)
        graph.order['modified'] = '2026-11-09 11:00:00'
        with pytest.raises(MESContextChanged):
            await connector.list_job_cards(graph.order['name'], 'ROW-A', scope=scope(), cursor=first.next_cursor, limit=1)
    run_graph(graph, scenario)


@pytest.mark.parametrize('where', ['operation', 'job_card'])
def test_nested_projection_never_reflects_upstream_credentials(where):
    graph = ERPGraph()
    if where == 'operation': graph.order['operations'][0]['status'] = SECRET
    else: graph.cards['JC-A']['status'] = SECRET
    async def scenario(connector):
        with pytest.raises(MESError):
            await connector.read_context(graph.order['name'], scope=scope(), operation_id='ROW-A', job_card_id='JC-A')
    run_graph(graph, scenario)


def test_versioned_contract_does_not_change_frozen_legacy_payloads():
    payload = dict(question='工单信息？', work_order_id='WO-017', operation_id='ROW-A', job_card_id='JC-A')
    assert ManufacturingExecutionQuestion.model_validate(payload).job_card_id == 'JC-A'
    with pytest.raises(ValidationError): ManufacturingQuestion.model_validate(payload)
    with pytest.raises(ValidationError): ManufacturingExecutionQuestion.model_validate(payload | dict(operation_id=None))
    from scripts.check_manufacturing_workorder_profile import check
    assert check()['status'] == 'passed'


@pytest.fixture
def execution_harness(harness):
    c = harness.c
    graph = ERPGraph('WO-017')
    connector, app = connector_for_graph(graph, tenant=c.tenant_id, source='mock-mes-hc')
    c.app.state.mes_connectors[c.tenant_id] = connector
    with TestClient(c.app) as client:
        harness.client = client
        harness.graph = graph
        try:
            yield harness
        finally:
            client.portal.call(connector.aclose)
            client.portal.call(app.state.erpnext_client.aclose)


def test_http_selected_time_uses_card_source_and_keeps_work_order_version_basis(execution_harness):
    harness = execution_harness
    graph = harness.graph
    response = harness.client.post('/api/manufacturing/qa', json=dict(question='所选作业卡实际开工时间是什么？',
        work_order_id='WO-017', operation_id='ROW-A', job_card_id='JC-A'))
    assert response.status_code == 200
    body = response.json()
    assert body['answerable'] and body['applicability']['business_revision'] == '1.0'
    assert body['work_order_context']['actual_started_at'].startswith('2026-09-28')
    cited = next(row for row in body['business_citations'] if row['field_name'] == 'job_card_actual_started_at')
    assert cited['value'] == '2026-10-26T09:30:00+08:00' and cited['source_entity'] == 'Job Card'
    assert cited['source_record_id'] == cited['job_card_id'] == 'JC-A' and cited['operation_id'] == 'ROW-A'
    assert len(graph.calls) == 9 and len(harness.generation.calls) == 0
    snapshot = harness.client.get('/api/qa/runs/' + body['run_id'])
    assert snapshot.status_code == 200 and snapshot.json()['snapshot']['manufacturing']['business_context']['record']['execution']['job_card_id'] == 'JC-A'


def test_http_missing_start_and_missing_card_do_not_fall_back_to_work_order_time(execution_harness):
    harness = execution_harness
    common = dict(work_order_id='WO-017', operation_id='ROW-B')
    response = harness.client.post('/api/manufacturing/qa', json=dict(**common, job_card_id='JC-B', question='所选作业卡实际开工时间是什么？'))
    assert response.status_code == 200 and response.json()['answerable']
    assert '未知' in response.json()['answer'] and not any(row['field_name']=='actual_started_at' for row in response.json()['business_citations'])
    response = harness.client.post('/api/manufacturing/qa', json=dict(**common, question='所选作业卡实际开工时间是什么？'))
    assert response.status_code == 200 and response.json()['response_type'] == 'clarify'


def test_card_citation_precision_uses_card_scope_instead_of_work_order(execution_harness):
    h = execution_harness
    h.graph.cards['JC-A']['actual_start_date'] = '2026-10-26'
    response = h.client.post('/api/manufacturing/qa', json=dict(question='所选作业卡实际开工时间是什么？',
        work_order_id='WO-017', operation_id='ROW-A', job_card_id='JC-A'))
    assert response.status_code == 200
    assert response.json()['work_order_context']['actual_start_precision'] == 'datetime'
    cited = next(row for row in response.json()['business_citations'] if row['field_name'] == 'job_card_actual_started_at')
    assert cited['value'] == '2026-10-26' and cited['actual_start_precision'] == 'date'


def test_conversation_binding_rejects_another_card_in_same_operation(execution_harness):
    harness = execution_harness
    request = dict(work_order_id='WO-017', operation_id='ROW-A', job_card_id='JC-A')
    created = harness.client.post('/api/manufacturing/conversations', json=request)
    assert created.status_code == 201 and created.json()['binding']['job_card_id'] == 'JC-A'
    response = harness.client.post('/api/manufacturing/qa', json=dict(request | dict(job_card_id='JC-A2'),
        conversation_id=created.json()['id'], question='所选作业卡实际开工时间是什么？'))
    assert response.status_code == 409 and not harness.generation.calls


def test_invalid_selection_body_is_rejected_before_source_read(execution_harness):
    harness = execution_harness
    graph = harness.graph
    response = harness.client.post('/api/manufacturing/qa', json=dict(work_order_id='WO-017', job_card_id='JC-A', question='开工时间？'))
    assert response.status_code == 422 and graph.calls == []


@pytest.mark.parametrize('question', ['请把所选作业卡登记为已经开工。', '请更新所选作业卡的开工时间。', '请直接暂停所选工序。'])
def test_selected_entity_execution_is_refused_without_generation(execution_harness, question):
    h = execution_harness
    response = h.client.post('/api/manufacturing/qa', json=dict(work_order_id='WO-017',
        operation_id='ROW-A', job_card_id='JC-A', question=question))
    assert response.status_code == 200 and response.json()['response_type'] == 'refuse'
    assert len(h.graph.calls) == 9 and not h.generation.calls


def test_unselected_card_change_does_not_invalidate_selected_context():
    graph = ERPGraph()
    async def scenario(connector):
        original = await connector.read_context(graph.order['name'], scope=scope(), operation_id='ROW-A', job_card_id='JC-A')
        graph.cards['JC-A2']['modified'] = '2026-11-09 12:00:00'
        checked = await connector.verify_context(original, scope=scope())
        assert checked.record == original.record
    run_graph(graph, scenario)


def test_selection_list_endpoints_authorize_before_source_and_expose_no_write(execution_harness):
    h = execution_harness
    assert h.client.get('/api/manufacturing/work-orders/WO-017/operations').status_code == 200
    assert h.client.get('/api/manufacturing/work-orders/WO-017/operations/ROW-A/job-cards').status_code == 200
    from test_document_upload_api import _set_principal
    _set_principal(h.c, h.c.viewer)
    before = len(h.graph.calls)
    for path in ('/api/manufacturing/work-orders/WO-017/operations', '/api/manufacturing/work-orders/WO-017/operations/ROW-A/job-cards'):
        assert h.client.get(path).status_code == 404
        for method in ('POST', 'PUT', 'DELETE'):
            assert h.client.request(method, path).status_code == 405
    assert len(h.graph.calls) == before
