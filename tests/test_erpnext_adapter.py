import asyncio
from copy import deepcopy
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from backend.app.integrations.erpnext import ERPNextConfig, project_work_order
from backend.app.integrations.mes import MESConnector, MESContextChanged, MESInvalidCursor, MESNotFound
from backend.app.integrations.mes_contracts import MESSourceConfig
from backend.app.security.business_scope import BusinessScope
from services.erpnext_adapter.app import create_app

TENANT = UUID('11111111-1111-4111-8111-111111111111')
TOKEN = 'erpnext-adapter-test-source-token-00000001'
KEY = 'erpnext_test_api_key'
SECRET = 'erpnext_test_api_secret'


def config():
    return ERPNextConfig(tenant_id=TENANT, source_system_id='erpnext-hc', base_url='https://erp.example.invalid',
        company_plants={'HC Lab': 'HC-P01', 'Other Lab': 'HC-P02'},
        api_key=SecretStr(KEY), api_secret=SecretStr(SECRET), read_token=SecretStr(TOKEN))


def order(**changes):
    return dict(name='MFG-WO-2026-00001', company='HC Lab', production_item='HC-A17', operations=[{'operation': 'OP-20'}],
        actual_start_date='2026-11-09 08:10:00', planned_start_date='2026-10-01 00:00:00',
        modified='2026-11-09 08:11:00.123456', status='In Process') | changes


def scope(plants=('HC-P01',)):
    return BusinessScope(TENANT, uuid4(), 'erpnext-hc', frozenset(plants))


def headers(**changes):
    return {'Authorization': 'Bearer ' + TOKEN, 'X-MES-Tenant': str(TENANT), 'X-MES-Plants': 'HC-P01'} | changes


def run_with_source(handler, scenario):
    async def run():
        app = create_app(config(), transport=httpx.MockTransport(handler))
        source = MESSourceConfig(tenant_id=TENANT, source_system_id='erpnext-hc', base_url='http://erpnext-adapter',
            allowed_plants=('HC-P01', 'HC-P02'), token=SecretStr(TOKEN))
        try:
            async with MESConnector(source, transport=httpx.ASGITransport(app)) as connector:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://adapter') as http:
                    await scenario(connector, http)
        finally:
            await app.state.erpnext_client.aclose()
    asyncio.run(run())


def test_real_contract_roundtrip_and_changed_revision_stop_old_context():
    raw = order()
    requests = []
    def upstream(request):
        requests.append(request)
        assert request.method == 'GET' and request.url.path == '/api/resource/Work Order/' + raw['name']
        assert request.headers['Authorization'] == 'token ' + KEY + ':' + SECRET
        return httpx.Response(200, json={'data': raw})
    async def scenario(connector, _):
        access = scope()
        old = await connector.read_context(raw['name'], scope=access)
        assert old.record.operation_code == 'OP-20'
        assert old.record.actual_started_at == '2026-11-09T08:10:00+08:00'
        assert old.record.source_record_id == raw['name']
        assert old.record.route_revision is None and old.record.batch_no is None and old.record.change_notice_refs == ()
        checked = await connector.verify_context(old, scope=access)
        assert checked.record == old.record and checked.fetched_at == old.fetched_at
        raw['actual_start_date'] = '2026-11-09 08:15:00'
        with pytest.raises(MESContextChanged):
            await connector.verify_context(old, scope=access)
    run_with_source(upstream, scenario)
    assert len(requests) == 3


@pytest.mark.parametrize('actual,expected,precision', [(None, None, None), ('', None, None),
    ('2026-11-09', '2026-11-09', 'date'), ('2026-11-09T00:10:00Z', '2026-11-09T00:10:00+00:00', 'datetime')])
def test_planned_date_never_substitutes_for_actual_start(actual, expected, precision):
    record = project_work_order(order(actual_start_date=actual), config(), {'HC-P01'})
    assert record.actual_started_at == expected and record.actual_start_precision == precision


@pytest.mark.parametrize('operations', [[], [{'operation': 'OP-20'}, {'operation': 'OP-30'}],
    [{'operation': 'OP-20'}, {'operation': 'OP-20'}]])
def test_no_implicit_first_operation_when_context_is_not_unique(operations):
    record = project_work_order(order(operations=operations), config(), {'HC-P01'})
    assert record.operation_code is None


@pytest.mark.parametrize('change,status', [({'Authorization': 'Bearer wrong'}, 401),
    ({'X-MES-Tenant': str(uuid4())}, 404), ({'X-MES-Plants': 'HC-P99'}, 404), ({'X-MES-Plants': ''}, 404)])
def test_adapter_authorization_precedes_any_erp_call(change, status):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={'data': order()})
    async def scenario(_, http):
        for path in ['/v1/work-orders', '/v1/work-orders/MFG-WO-2026-00001/context']:
            response = await http.get(path, headers=headers(**change))
            assert response.status_code == status
    run_with_source(upstream, scenario)
    assert calls == []


def test_read_service_has_no_write_routes_or_upstream_write_calls():
    calls = []
    async def scenario(_, http):
        for method in ['POST', 'PUT', 'PATCH', 'DELETE']:
            response = await http.request(method, '/v1/work-orders/MFG-WO-2026-00001/context', headers=headers(), json={'status': 'Completed'})
            assert response.status_code == 405
    run_with_source(lambda request: calls.append(request), scenario)
    assert calls == []


def test_cross_company_result_is_not_exposed_even_with_valid_source_token():
    async def scenario(connector, _):
        with pytest.raises(MESNotFound):
            await connector.read_context('MFG-WO-2026-00001', scope=scope())
    run_with_source(lambda _: httpx.Response(200, json={'data': order(company='Other Lab')}), scenario)


def test_cursor_binds_company_scope_limit_and_only_returns_requested_page():
    requests = []
    data = [order(name=f'MFG-WO-2026-{i:05}') for i in range(1, 4)]
    def upstream(request):
        import json
        requests.append(request)
        if request.url.path == '/api/resource/Work Order':
            filters = json.loads(request.url.params['filters'])
            assert filters[0] == ['company', 'in', ['HC Lab']]
            return httpx.Response(200, json={'data': [{'name': row['name']} for row in data if row['name'] > filters[1][2]][:int(request.url.params['limit_page_length'])]})
        return httpx.Response(200, json={'data': next(row for row in data if request.url.path.endswith('/' + row['name']))})
    async def scenario(connector, _):
        access = scope()
        first = await connector.list_work_orders(scope=access, limit=1)
        second = await connector.list_work_orders(scope=access, cursor=first.next_cursor, limit=1)
        assert first.items[0].work_order_id.endswith('00001') and second.items[0].work_order_id.endswith('00002')
        for cursor, limit, plants in [(first.next_cursor + 'x', 1, ('HC-P01',)),
                                      (first.next_cursor, 2, ('HC-P01',)), (first.next_cursor, 1, ('HC-P02',))]:
            with pytest.raises(MESInvalidCursor):
                await connector.list_work_orders(scope=scope(plants), cursor=cursor, limit=limit)
    run_with_source(upstream, scenario)
    assert len(requests) == 4 and all(request.method == 'GET' for request in requests)


@pytest.mark.parametrize('status,expected', [(401, 503), (403, 404), (404, 404), (429, 503), (500, 503), (302, 502)])
def test_upstream_error_is_redacted_without_retry(status, expected):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(status, text=SECRET, headers={'Location': 'https://example.invalid/steal'})
    async def scenario(_, http):
        response = await http.get('/v1/work-orders/MFG-WO-2026-00001/context', headers=headers())
        assert response.status_code == expected and SECRET not in response.text and KEY not in response.text
    run_with_source(upstream, scenario)
    assert len(calls) == 1


@pytest.mark.parametrize('change', [dict(actual_start_date=False), dict(actual_start_date='invalid'), dict(modified=None),
    dict(operations=None), dict(production_item=SECRET), dict(name='OTHER-WO')])
def test_malformed_or_secret_bearing_source_cannot_be_delivered(change):
    async def scenario(_, http):
        response = await http.get('/v1/work-orders/MFG-WO-2026-00001/context', headers=headers())
        assert response.status_code == 502 and SECRET not in response.text
    run_with_source(lambda _: httpx.Response(200, json={'data': order(**change)}), scenario)


def test_revision_is_stable_and_tracks_actual_facts_and_upstream_modification():
    raw = order()
    first = project_work_order(raw, config(), {'HC-P01'})
    assert first == project_work_order(deepcopy(raw), config(), {'HC-P01'})
    for change in [dict(production_item='HC-A18'), dict(actual_start_date='2026-11-09 09:00:00'),
                   dict(modified='2026-11-09 09:01:00'), dict(status='Completed')]:
        assert project_work_order(raw | change, config(), {'HC-P01'}).source_revision != first.source_revision


def test_page_preserves_erp_database_order_for_mixed_case_names():
    names = ['a-WO', 'B-WO']
    def upstream(request):
        if request.url.path == '/api/resource/Work Order':
            assert request.url.params['order_by'] == 'name asc'
            return httpx.Response(200, json={'data': [{'name': name} for name in names]})
        return httpx.Response(200, json={'data': order(name=request.url.path.rsplit('/', 1)[1])})
    async def scenario(connector, _):
        page = await connector.list_work_orders(scope=scope(), limit=2)
        assert [item.work_order_id for item in page.items] == names
    run_with_source(upstream, scenario)


def test_endpoint_configuration_is_server_only_and_http_is_narrow():
    values = config().model_dump()
    for change in [dict(base_url='http://public.example.com'), dict(base_url='https://erp.invalid/?url=x'),
                   dict(base_url='https://user:pass@erp.invalid'), dict(base_url='http://@frontend'), dict(site_timezone='UTC')]:
        with pytest.raises(ValidationError):
            ERPNextConfig.model_validate(values | change)
