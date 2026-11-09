"""Real quality flow with local ERP HTTP double; no paid model calls."""
import asyncio
from copy import deepcopy
import json
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from test_document_upload_api import api_context, _set_principal
from test_manufacturing_m4 import m4, TEST_SECRET, dataset
from test_erpnext_execution import ERPGraph
from test_erpnext_adapter import config
from services.erpnext_adapter.app import create_app
from backend.app.integrations.mes import MESConnector
from backend.app.manufacturing.evaluation_sources import EvaluationSources
from backend.app.manufacturing.source_registration import erpnext_source_descriptor, LIVE_SOURCE_CONTRACT
from backend.app.db.business_models import BusinessScopeGrant
from backend.app.db.quality_models import QARun
from backend.app.quality.evaluation_worker import QualityEvaluationWorker
from backend.app.evaluation.manufacturing_metrics import score_manufacturing


@pytest.fixture
def erp_quality(m4):
    h = m4
    first, second = ERPGraph('WO-017'), ERPGraph('WO-018')
    for row in second.order['operations']: row['name'] = '2-' + row['name']
    second.cards = {'2-' + name: row | dict(name='2-' + name, operation_id='2-' + row['operation_id']) for name, row in second.cards.items()}
    def upstream(request):
        graph = second if ('WO-018' in request.url.path or '/Job Card/2-' in request.url.path or
            request.url.path.endswith('/Job Card') and 'WO-018' in request.url.params.get('filters', '')) else first
        return graph(request)
    cfg = config().model_copy(update=dict(tenant_id=h.c.tenant_id, source_system_id='mock-mes-hc', read_token=SecretStr(TEST_SECRET)))
    adapter = create_app(cfg, transport=httpx.MockTransport(upstream))
    source_file = h.settings.manufacturing_sources_file
    sources = json.loads(source_file.read_text()); sources['sources'][0]['execution_selection'] = True
    source_file.write_text(json.dumps(sources))
    descriptor = erpnext_source_descriptor(cfg)
    h.entry = dict(tenant_id=str(h.c.tenant_id), source_system_id='mock-mes-hc', source_kind='live',
        source_contract=LIVE_SOURCE_CONTRACT, source_sha256=descriptor.configuration_sha256)
    h.registry.write_text(json.dumps(dict(schema_version='manufacturing-evaluation-registry-v2', active=[h.entry])))
    registry = EvaluationSources(h.settings, connector_factory=lambda source: MESConnector(source, transport=httpx.ASGITransport(adapter)))
    h.settings.manufacturing_evaluation_sources = registry
    source, _ = registry.registered(h.c.tenant_id)
    connector = registry.connector_factory(source)
    h.c.app.state.mes_connectors[h.c.tenant_id] = connector
    h.erp_graphs = (first, second)
    try: yield h
    finally:
        asyncio.run(connector.aclose()); asyncio.run(adapter.state.erpnext_client.aclose())


def approve(client, h, suffix='', selection=None, **change):
    wo = 'WO-018' if suffix else 'WO-017'
    selection = selection or dict(work_order_id=wo, operation_id=suffix+'ROW-A', job_card_id=suffix+'JC-A')
    question = '所选作业卡实际开工时间是什么？'
    answer = client.post('/api/manufacturing/qa', json=dict(question=question, **selection))
    assert answer.status_code == 200, answer.text
    run = answer.json()['run_id']
    assert client.put('/api/qa/runs/'+run+'/feedback', json=dict(rating='up', comment='助手非独立流程验收，不是真实用户缺陷。', share_with_admins=True, allow_dataset_export=True)).status_code == 200
    opened = client.post('/api/quality/cases', json=dict(run_id=run));assert opened.status_code == 201, opened.text
    body = opened.json()
    fields = {'operation_id','job_card_id','job_card_actual_started_at','job_card_start_precision'}
    keys = ['citation_id','field_name','value','source_system_id','source_record_id','source_revision','payload_sha256',
        'source_entity','operation_id','job_card_id','actual_start_precision']
    selected = [{k:row[k] for k in keys if k in row} for row in body['source']['business_evidence_options'] if row['field_name'] in fields]
    actual = next(row['value'] for row in selected if row['field_name']=='job_card_actual_started_at') or '未知'
    payload = dict(expected_revision=1, review_status='confirmed', sample_status='approved', root_cause='other',
        review_reason='助手非独立逐字段核对本地来源替身，非模型质量评分。', evaluation_question=question,
        expected_answerable=True, expected_behavior='answer', reference_answer='所选作业卡实际开工为'+actual+'。',
        required_facts=[actual], business_evidence=selected, evidence=[])
    payload.update(change)
    response = client.put('/api/quality/cases/'+body['case']['id'], json=payload)
    return body, payload, response


def test_live_erp_review_export_and_zero_budget_worker_keep_selection(erp_quality):
    h = erp_quality
    with TestClient(h.c.app) as client:
        versions=[]
        for suffix in ('','2-'):
            case, payload, saved = approve(client,h,suffix);assert saved.status_code == 200, saved.text
            versions.append(dataset(client,case))
        exported = client.get('/api/quality/datasets/'+versions[0]+'/samples');assert exported.status_code == 200, exported.text
        sample=json.loads(exported.text.strip())
        assert sample['business_context_binding']['source']['source_kind']=='live'
        assert 'fixture_version' not in sample['business_context_binding']['source']
        assert sample['business_context_binding']['context']['record']['execution']['job_card_id']=='JC-A'
        with h.c.session_factory() as db:before=db.scalar(select(func.count()).select_from(QARun))
        queued=client.post('/api/quality/evaluations',json=dict(fixed_dataset_id=versions[0], feedback_dataset_id=versions[1],
            allow_model_calls=False,max_cases=2,max_model_calls=0,max_tokens=0,max_seconds=120))
        assert queued.status_code==201,queued.text
        assert QualityEvaluationWorker(h.c.session_factory,h.settings,h.answer).run_once()
        job=client.get('/api/quality/evaluations/'+queued.json()['id']).json()
        assert job['status']=='succeeded',job
        assert len(job['records'])==2 and all(row['proxy_pass'] for row in job['records']),job
        assert not h.generation.calls and job['summary']['budget']['calls']==0
        for row in job['records']:
            assert row['metrics']['manufacturing_scorer']=='manufacturing-execution-v1'
            assert row['manufacturing_observation']['validation']['mes_reads']==3
            assert row['manufacturing_observation']['business_context']['record']['execution']['job_card_id'].endswith('JC-A')
        with h.c.session_factory() as db:assert db.scalar(select(func.count()).select_from(QARun))==before


@pytest.mark.parametrize('field,value',[('source_entity','Work Order'),('source_record_id','JC-B'),('operation_id','ROW-B'),('job_card_id','JC-B'),('job_card_id',None),('actual_start_precision','date')])
def test_execution_review_rejects_forged_field_identity(erp_quality, field, value):
    h=erp_quality
    with TestClient(h.c.app) as client:
        case,body,saved=approve(client,h);assert saved.status_code==200,saved.text
        body['expected_revision']=2
        row=next(x for x in body['business_evidence'] if x['field_name']=='job_card_actual_started_at');row[field]=value
        assert client.put('/api/quality/cases/'+case['case']['id'],json=body).status_code==409


@pytest.mark.parametrize('change',['card','registration','review','consent','unrelated_card'])
def test_live_frozen_export_rechecks_exact_source_permissions_and_consent(erp_quality,change):
    h=erp_quality
    with TestClient(h.c.app) as client:
        case,body,saved=approve(client,h);assert saved.status_code==200,saved.text
        version=dataset(client,case)
        if change=='card':h.erp_graphs[0].cards['JC-A']['actual_start_date']='2026-10-27 09:30:00'
        elif change=='unrelated_card':h.erp_graphs[0].cards['JC-A2']['modified']='2026-11-09 12:00:00'
        elif change=='registration':
            data=json.loads(h.registry.read_text());data['active'][0]['source_sha256']='a'*64;h.registry.write_text(json.dumps(data))
        elif change=='review':
            with h.c.session_factory() as db:db.execute(delete(BusinessScopeGrant).where(BusinessScopeGrant.permission=='review'));db.commit()
        else:client.put('/api/qa/runs/'+case['source']['run_id']+'/feedback',json=dict(rating='up',share_with_admins=True,allow_dataset_export=False))
        result=client.get('/api/quality/datasets/'+version+'/samples')
        assert result.status_code==(200 if change=='unrelated_card' else 404 if change in ('review','consent') else 409),result.text


def test_execution_scorer_rejects_omitted_or_wrong_card_citations(erp_quality):
    h=erp_quality
    with TestClient(h.c.app) as client:
        case,body,saved=approve(client,h);assert saved.status_code==200,saved.text
        sample=json.loads(client.get('/api/quality/datasets/'+dataset(client,case)+'/samples').text)
        response=case['source']['response'];assert score_manufacturing(sample,response)['automatic_proxy_pass']
        for mode in ('missing','card','selection'):
            bad=deepcopy(response)
            if mode=='missing':bad['business_citations']=[]
            elif mode=='card':bad['business_citations'][0]['source_record_id']='JC-B'
            else:bad['work_order_context']['execution']['job_card_id']='JC-B'
            assert not score_manufacturing(sample,bad)['automatic_proxy_pass']


def test_different_cards_in_same_work_order_cannot_split_fixed_and_feedback(erp_quality):
    h=erp_quality
    with TestClient(h.c.app) as client:
        versions=[]
        for card in ('JC-A','JC-A2'):
            case,body,saved=approve(client,h,selection=dict(work_order_id='WO-017',operation_id='ROW-A',job_card_id=card))
            assert saved.status_code==200,saved.text
            versions.append(dataset(client,case))
        result=client.post('/api/quality/evaluations',json=dict(fixed_dataset_id=versions[0],feedback_dataset_id=versions[1],
            allow_model_calls=False,max_model_calls=0,max_tokens=0))
        assert result.status_code==409 and not h.generation.calls


def test_queued_live_job_stops_before_model_when_selected_card_changes(erp_quality):
    from backend.app.db.verification_models import QualityEvaluationJob
    h=erp_quality
    with TestClient(h.c.app) as client:
        versions=[]
        for suffix in ('','2-'):
            case,body,saved=approve(client,h,suffix);assert saved.status_code==200,saved.text
            versions.append(dataset(client,case))
        queued=client.post('/api/quality/evaluations',json=dict(fixed_dataset_id=versions[0],feedback_dataset_id=versions[1],
            allow_model_calls=False,max_model_calls=0,max_tokens=0))
        assert queued.status_code==201,queued.text
        h.erp_graphs[0].cards['JC-A']['modified']='2026-11-09 13:00:00'
        assert QualityEvaluationWorker(h.c.session_factory,h.settings,h.answer).run_once()
        with h.c.session_factory() as db:
            job=db.get(QualityEvaluationJob,UUID(queued.json()['id']))
            assert job.status=='failed' and not job.records_json and job.summary_json['budget']['calls']==0
        assert not h.generation.calls
