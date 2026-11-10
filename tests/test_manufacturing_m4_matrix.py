"""Fault matrix through formal HTTP handlers, each with a legal positive control."""
from uuid import uuid4
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func,select

from test_document_upload_api import api_context
from test_manufacturing_m4 import m4
from manufacturing_m3_helpers import qa_input,TEST_SECRET
from backend.app.db.quality_models import QARun
from backend.app.db.model_usage_models import ModelUsageEvent
from backend.app.evaluation.trace import capture_trace


@pytest.mark.parametrize('case,status,generation',[('timeout',503,0),('unavailable',503,0),('credential',503,0),
    ('missing',404,0),('conditions',200,0),('tenant',502,0),('plant',502,0),('type',502,0),('date',502,0),
    ('oversized',502,0),('truncated',502,0),('before_revision',409,0),('after_revision',409,1),('same_revision',502,0),('final_failure',503,1)])
def test_formal_http_faults_stop_delivery_preserve_usage_and_have_positive_control(m4,case,status,generation,request):
    h=m4
    with TestClient(h.c.app) as client:
        positive=client.post('/api/manufacturing/qa',json=qa_input(h))
        assert positive.status_code==200 and positive.json()['answerable'],positive.text
        h.source.reads.clear();h.generation.calls.clear()
        with h.c.session_factory() as session:
            before_runs=session.scalar(select(func.count()).select_from(QARun))
            before_usage=session.scalar(select(func.count()).select_from(ModelUsageEvent))
        payload=qa_input(h)
        if case=='timeout':
            def fail(_):raise httpx.ReadTimeout('synthetic timeout')
            h.source.failure=fail
        elif case in ('unavailable','credential'):
            h.source.failure=lambda _:httpx.Response(503 if case=='unavailable' else 401,content=TEST_SECRET)
        elif case=='missing':payload=qa_input(h,wo='WO-NOT-REGISTERED')
        elif case=='conditions':payload=qa_input(h,wo='WO-020',question='当前工单应执行哪个业务版本？')
        elif case in ('tenant','plant','type','date'):
            record=dict(h.source.records['WO-018'])
            record.update({'tenant_id':str(uuid4())} if case=='tenant' else {'plant_code':'HC-P02'} if case=='plant'
                else {'source_revision':123} if case=='type' else {'actual_started_at':'2026-09-28T00:00:00','actual_start_precision':'datetime'})
            h.source.failure=lambda _:httpx.Response(200,json=record)
        elif case=='oversized':h.source.failure=lambda _:httpx.Response(200,content=b'x'*65537,headers={'content-type':'application/json'})
        elif case=='truncated':h.source.failure=lambda _:httpx.Response(200,content=b'{}',headers={'content-type':'application/json','content-length':'10'})
        else:
            at=3 if case in ('after_revision','final_failure') else 2
            def change(source,count):
                if count!=at:return
                if case=='final_failure':source.failure=lambda _:httpx.Response(503)
                elif case=='same_revision':source.records['WO-018']['batch_no']='SYNTHETIC-CHANGED'
                else:source.records['WO-018']['source_revision']='r2'
            h.source.hook=change
        with capture_trace() as trace:
            response=client.post('/api/manufacturing/qa',json=payload)
        assert response.status_code==status,response.text
        assert response.headers.get('x-request-id') and response.headers['cache-control']=='no-store'
        assert TEST_SECRET not in response.text
        assert len(h.generation.calls)==generation
        if case=='conditions':assert response.json()['response_type']=='clarify'
        with h.c.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(QARun))==before_runs+(case=='conditions')
            if session.get_bind().dialect.name=='postgresql':
                assert session.scalar(select(func.count()).select_from(ModelUsageEvent))==before_usage+generation
        usage=trace.to_dict()['stages'].get('model_usage',[])
        assert len(usage)==generation and all(row['total_tokens']==50 for row in usage)
        request.node.manufacturing_observations=dict(fault=case,negative_trace=trace.to_dict(),
            provider_calls=generation,saved_clarification=case=='conditions',success_answer_saved=False)
        assert len(h.source.reads)<=3
