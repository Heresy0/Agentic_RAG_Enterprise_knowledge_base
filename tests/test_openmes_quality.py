"""OpenMES live registration, exact field evidence and frozen zero-call Worker."""
import asyncio
from copy import deepcopy
import json
from uuid import UUID
import httpx
from pydantic import SecretStr
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete,select,func
from test_manufacturing_m4 import m4,api_context,dataset
from test_openmes_adapter import Graph,config
from manufacturing_m3_helpers import TEST_SECRET
from services.openmes_adapter.app import create_app
from backend.app.integrations.mes import MESConnector
from backend.app.manufacturing.evaluation_sources import EvaluationSources
from backend.app.manufacturing.source_registration import OPENMES_SOURCE_CONTRACT
from backend.app.db.business_models import BusinessScopeGrant
from backend.app.db.quality_models import QARun
from backend.app.quality.evaluation_worker import QualityEvaluationWorker
from backend.app.evaluation.manufacturing_metrics import score_manufacturing


@pytest.fixture
def openmes_quality(m4):
    h=m4;g=Graph();g.orders['2']['line_id']=1
    cfg=config(tenant_id=h.c.tenant_id,source_system_id='mock-mes-hc',read_token=SecretStr(TEST_SECRET))
    adapter=create_app(cfg,transport=httpx.MockTransport(g))
    sources=json.loads(h.settings.manufacturing_sources_file.read_text());sources['sources'][0]['batch_step_selection']=True
    h.settings.manufacturing_sources_file.write_text(json.dumps(sources))
    h.entry=dict(tenant_id=str(h.c.tenant_id),source_system_id='mock-mes-hc',source_kind='live',
        source_contract=OPENMES_SOURCE_CONTRACT,source_sha256=adapter.state.openmes_client.configuration_sha256)
    h.registry.write_text(json.dumps(dict(schema_version='manufacturing-evaluation-registry-v2',active=[h.entry])))
    registry=EvaluationSources(h.settings,connector_factory=lambda source:MESConnector(source,transport=httpx.ASGITransport(adapter)))
    h.settings.manufacturing_evaluation_sources=registry
    source,_=registry.registered(h.c.tenant_id);connector=registry.connector_factory(source)
    h.c.app.state.mes_connectors[h.c.tenant_id]=connector;h.graph=g
    try:yield h
    finally:asyncio.run(connector.aclose());asyncio.run(adapter.state.openmes_client.aclose())


def approve(client,h,wo='1',step=None,entity='step',**change):
    step=step or str(int(wo)*10+1)
    selection=dict(work_order_id=wo,batch_id=wo,step_id=step if entity=='step' else None)
    question='所选'+('步骤' if entity=='step' else '批次')+'的实际开工时间是什么？'
    response=client.post('/api/manufacturing/qa',json=dict(selection,question=question));assert response.status_code==200,response.text
    run=response.json()['run_id']
    assert client.put('/api/qa/runs/'+run+'/feedback',json=dict(rating='up',comment='助手非独立流程验收，不是工厂缺陷。',share_with_admins=True,allow_dataset_export=True)).status_code==200
    opened=client.post('/api/quality/cases',json=dict(run_id=run));assert opened.status_code==201,opened.text
    case=opened.json();fields={'work_order_id','batch_id',entity+'_actual_started_at',entity+'_start_precision'} | ({'step_id'} if entity=='step' else set())
    keys=['citation_id','field_name','value','source_system_id','source_record_id','source_revision','payload_sha256','source_entity','batch_id','step_id','actual_start_precision']
    selected=[{k:row[k] for k in keys if k in row} for row in case['source']['business_evidence_options'] if row['field_name'] in fields]
    actual=next(row['value'] for row in selected if row['field_name']==entity+'_actual_started_at') or '未知'
    body=dict(expected_revision=1,review_status='confirmed',sample_status='approved',root_cause='other',
        review_reason='助手非独立逐字段核对来源替身，不计模型质量。',evaluation_question=question,expected_answerable=True,expected_behavior='answer',
        reference_answer='所选步骤实际开工为'+actual+'。',required_facts=[actual],business_evidence=selected,evidence=[])
    body.update(change)
    saved=client.put('/api/quality/cases/'+case['case']['id'],json=body)
    return case,body,saved


def test_openmes_review_frozen_samples_and_zero_worker_preserve_batch_step(openmes_quality):
    h=openmes_quality
    with TestClient(h.c.app) as c:
        versions=[]
        for wo in ('1','2'):
            case,body,saved=approve(c,h,wo);assert saved.status_code==200,saved.text
            versions.append(dataset(c,case))
        exported=c.get('/api/quality/datasets/'+versions[0]+'/samples');assert exported.status_code==200,exported.text
        sample=json.loads(exported.text);assert sample['business_context_binding']['source']['source_contract']==OPENMES_SOURCE_CONTRACT
        assert sample['business_context_binding']['context']['record']['step']['step_id']=='11'
        with h.c.session_factory() as db:before=db.scalar(select(func.count()).select_from(QARun))
        job=c.post('/api/quality/evaluations',json=dict(fixed_dataset_id=versions[0],feedback_dataset_id=versions[1],allow_model_calls=False,max_cases=2,max_model_calls=0,max_tokens=0,max_seconds=120))
        assert job.status_code==201,job.text
        assert QualityEvaluationWorker(h.c.session_factory,h.settings,h.answer,allow_model_calls=False).run_once()
        result=c.get('/api/quality/evaluations/'+job.json()['id']).json();assert result['status']=='succeeded',result
        assert all(x['proxy_pass'] and x['metrics']['manufacturing_scorer']=='manufacturing-openmes-v1' for x in result['records'])
        assert all(x['manufacturing_observation']['validation']['mes_reads']==3 for x in result['records'])
        assert result['summary']['budget']['calls']==0 and not h.generation.calls
        with h.c.session_factory() as db:assert db.scalar(select(func.count()).select_from(QARun))==before


def test_batch_only_sample_keeps_batch_entity_without_inventing_a_step(openmes_quality):
    h=openmes_quality
    with TestClient(h.c.app) as c:
        case,body,saved=approve(c,h,entity='batch');assert saved.status_code==200,saved.text
        sample=json.loads(c.get('/api/quality/datasets/'+dataset(c,case)+'/samples').text)
        assert sample['business_context_binding']['context']['record']['step'] is None
        refs=sample['expected_business_evidence'];assert all('step_id' not in x for x in refs)
        assert next(x for x in refs if x['field_name']=='batch_actual_started_at')['source_entity']=='Batch'
        assert score_manufacturing(sample,case['source']['response'])['automatic_proxy_pass']


def test_guidance_label_cannot_be_approved_without_a_reviewed_step_assignment(openmes_quality):
    h=openmes_quality
    with TestClient(h.c.app) as c:
        case,body,saved=approve(c,h);assert saved.status_code==200,saved.text
        body.update(expected_revision=2,evaluation_question='所选步骤应该使用哪份指导书？')
        assert c.put('/api/quality/cases/'+case['case']['id'],json=body).status_code==409


@pytest.mark.parametrize('key,value',[('source_entity','Work Order'),('source_record_id','12'),('batch_id','2'),('step_id','12'),('step_id',None),('actual_start_precision','date'),('value','fake')])
def test_step_review_rejects_forged_entity_selection_time_and_value(openmes_quality,key,value):
    h=openmes_quality
    with TestClient(h.c.app) as c:
        case,body,saved=approve(c,h);assert saved.status_code==200,saved.text
        body['expected_revision']=2
        next(x for x in body['business_evidence'] if x['field_name']=='step_actual_started_at')[key]=value
        assert c.put('/api/quality/cases/'+case['case']['id'],json=body).status_code==409


@pytest.mark.parametrize('change',['source','registration','review','consent','unrelated_step'])
def test_frozen_openmes_rechecks_source_registration_access_and_consent(openmes_quality,change):
    h=openmes_quality
    with TestClient(h.c.app) as c:
        case,body,saved=approve(c,h);assert saved.status_code==200,saved.text
        version=dataset(c,case)
        if change=='source':h.graph.batches['1']['steps'][0]['status']='DONE'
        elif change=='unrelated_step':h.graph.batches['1']['steps'][1]['status']='DONE'
        elif change=='registration':
            data=json.loads(h.registry.read_text());data['active'][0]['source_sha256']='a'*64;h.registry.write_text(json.dumps(data))
        elif change=='review':
            with h.c.session_factory() as db:db.execute(delete(BusinessScopeGrant).where(BusinessScopeGrant.permission=='review'));db.commit()
        else:c.put('/api/qa/runs/'+case['source']['run_id']+'/feedback',json=dict(rating='up',share_with_admins=True,allow_dataset_export=False))
        result=c.get('/api/quality/datasets/'+version+'/samples')
        assert result.status_code==(200 if change=='unrelated_step' else 404 if change in ('review','consent') else 409),result.text


def test_same_work_order_different_steps_cannot_split_sets_and_scorer_checks_identity(openmes_quality):
    h=openmes_quality
    with TestClient(h.c.app) as c:
        versions=[]
        for step in ('11','12'):
            case,body,saved=approve(c,h,step=step);assert saved.status_code==200,saved.text
            version=dataset(c,case);versions.append(version)
            sample=json.loads(c.get('/api/quality/datasets/'+version+'/samples').text)
            assert score_manufacturing(sample,case['source']['response'])['automatic_proxy_pass']
            for mode in ('citations','selection'):
                bad=deepcopy(case['source']['response'])
                if mode=='citations':bad['business_citations']=[]
                else:bad['work_order_context']['step']['step_id']='99'
                assert not score_manufacturing(sample,bad)['automatic_proxy_pass']
        result=c.post('/api/quality/evaluations',json=dict(fixed_dataset_id=versions[0],feedback_dataset_id=versions[1],allow_model_calls=False,max_model_calls=0,max_tokens=0))
        assert result.status_code==409
