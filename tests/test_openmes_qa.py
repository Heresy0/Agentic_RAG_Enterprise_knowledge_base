"""OpenMES context, pipeline citations and immutable conversation selections."""
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from sqlalchemy import select,delete

from test_openmes_api import mes,harness,api_context,_set_principal
from backend.app.db.business_models import ConversationBusinessBinding,BusinessScopeGrant
from backend.app.manufacturing.batch_qa_contracts import BatchWorkOrderRecord
from backend.app.manufacturing.execution_contracts import ManufacturingExecutionQuestion,context_from_payload,selection_kwargs
from backend.app.manufacturing.answer_context import verified_business_citations
from backend.app.knowledge.answer_models import AnswerResult
from backend.app.integrations.mes import MESError


def request(h,**changes):
    return dict(dict(work_order_id='1',batch_id='1',step_id='11',question='所选步骤的实际开工时间是什么？',
        knowledge_base_id=str(h.c.knowledge_base_id)),**changes)


def conversation(c,h,**changes):
    data=request(h,**changes);data.pop('question')
    response=c.post('/api/manufacturing/conversations',json=data)
    assert response.status_code==201,response.text
    return response.json()['id']


@pytest.mark.parametrize('question,entity,field,known',[
    ('所选步骤的实际开工时间是什么？','Batch Step','step_actual_started_at',True),
    ('这个批次的实际开工时间是什么？','Batch','batch_actual_started_at',True),
    ('工单实际开工时间是什么？','Work Order','actual_started_at',False)])
def test_simple_time_queries_project_exact_entity_and_keep_three_rechecks(mes,monkeypatch,question,entity,field,known):
    h,g=mes
    def unexpected(*a,**k):raise AssertionError('已确定的业务字段不得进入生成、补查或修复')
    monkeypatch.setattr(h.answer,'_generate_draft',unexpected)
    monkeypatch.setattr(h.answer._evidence_feedback,'run',unexpected)
    monkeypatch.setattr(h.answer._answer_repair,'repair',unexpected)
    with TestClient(h.c.app) as c:
        response=c.post('/api/manufacturing/qa',json=request(h,question=question))
        assert response.status_code==200,response.text
        body=response.json();assert body['answerable'] and not h.generation.calls
        ref=next(x for x in body['business_citations'] if x['field_name']==field)
        assert ref['source_entity']==entity and ref['batch_id']=='1' and ref['step_id']=='11'
        assert (ref['value'] is not None)==known
        snapshot=c.get('/api/qa/runs/'+body['run_id']).json()['snapshot']
        frozen=snapshot['manufacturing'];assert frozen['validation']['mes_reads']==3
        context=context_from_payload(frozen['business_context'])
        assert isinstance(context.record,BatchWorkOrderRecord)
        assert selection_kwargs(context.record)=={'batch_id':'1','step_id':'11'}


@pytest.mark.parametrize('field,question,entity,source_id',[
    ('step_name','所选步骤的名称是什么？','Batch Step','11'),
    ('step_status','所选步骤的状态是什么？','Batch Step','11'),
    ('batch_lot_number','所选批次的批号是什么？','Batch','1'),
    ('work_order_number','工单编号是什么？','Work Order','1')])
def test_generation_field_selection_uses_backend_text_and_exact_source(mes,field,question,entity,source_id):
    h,g=mes;h.generation.field=field
    with TestClient(h.c.app) as c:
        response=c.post('/api/manufacturing/qa',json=request(h,question=question))
        assert response.status_code==200,response.text
        body=response.json();assert body['answerable'] and h.generation.calls
        ref=next(x for x in body['business_citations'] if x['field_name']==field)
        assert (ref['source_entity'],ref['source_record_id'])==(entity,source_id)
        assert ref['value'] in body['answer'] and not body['citations']


def test_unknown_step_time_missing_selection_and_guidance_do_not_infer_parent_time(mes):
    h,g=mes
    with TestClient(h.c.app) as c:
        unknown=c.post('/api/manufacturing/qa',json=request(h,step_id='12')).json()
        assert unknown['answerable'] and '未知' in unknown['answer']
        assert next(x for x in unknown['business_citations'] if x['field_name']=='step_actual_started_at')['value'] is None
        missing=c.post('/api/manufacturing/qa',json=request(h,batch_id=None,step_id=None)).json()
        assert missing['response_type']=='clarify' and '工序步骤' in missing['missing_fields']
        guide=c.post('/api/manufacturing/qa',json=request(h,question='当前步骤应该采用哪版指导书，首件要检查几件？')).json()
        assert guide['response_type']=='clarify' and not guide['answerable']
        assert '步骤指导书审核映射' in guide['missing_fields'] and not h.generation.calls


def test_conversation_binds_exact_selection_and_restores_frozen_context(mes):
    h,g=mes
    with TestClient(h.c.app) as c:
        identifier=conversation(c,h)
        for question in ('所选步骤的实际开工时间是什么？','这个批次的实际开工时间是什么？'):
            r=c.post('/api/manufacturing/qa',json=request(h,conversation_id=identifier,question=question));assert r.status_code==200,r.text
        restored=c.get('/api/manufacturing/conversations/'+identifier,params={'knowledge_base_id':str(h.c.knowledge_base_id)})
        assert restored.status_code==200,restored.text
        body=restored.json()
        assert (body['binding']['batch_id'],body['binding']['step_id'])==('1','11')
        assert len(body['turns'])==2 and body['turns'][0]['result']['work_order_context']['step']['step_id']=='11'
        assert c.post('/api/manufacturing/qa',json=request(h,conversation_id=identifier,step_id='12')).status_code==409
        assert c.post('/api/manufacturing/qa',json=request(h,conversation_id=identifier,batch_id=None,step_id=None)).status_code==409
    with h.c.session_factory() as db:
        binding=db.scalar(select(ConversationBusinessBinding));assert (binding.batch_id,binding.step_id)==('1','11')


def test_source_change_or_revocation_prevents_followup_and_historical_access(mes):
    h,g=mes
    with TestClient(h.c.app) as c:
        identifier=conversation(c,h)
        first=c.post('/api/manufacturing/qa',json=request(h,conversation_id=identifier)).json()
        assert c.get('/api/manufacturing/conversations/'+identifier,params={'knowledge_base_id':str(h.c.knowledge_base_id)}).status_code==200
        g.batches['1']['steps'][0]['started_at']='2026-10-03T00:00:00Z'
        assert c.post('/api/manufacturing/qa',json=request(h,conversation_id=identifier)).status_code==409
        with h.c.session_factory() as db:
            db.execute(delete(BusinessScopeGrant).where(BusinessScopeGrant.user_id==h.c.editor.user_id));db.commit()
        assert c.get('/api/manufacturing/conversations/'+identifier,params={'knowledge_base_id':str(h.c.knowledge_base_id)}).status_code==404
        assert c.get('/api/qa/runs/'+first['run_id']).status_code==404


@pytest.mark.parametrize('at', [3,5])
def test_selected_step_change_during_generation_or_delivery_is_not_delivered(mes,at):
    h,g=mes;count=0
    def hook(graph,req):
        nonlocal count
        if req.url.path=='/api/v1/work-orders/1':
            count+=1
            if count==at:graph.batches['1']['steps'][0]['status']='DONE'
    g.hook=hook
    with TestClient(h.c.app) as c:
        response=c.post('/api/manufacturing/qa',json=request(h))
        assert response.status_code==409,response.text


@pytest.mark.parametrize('question',['请直接开始当前步骤','请帮我完成这个批次','请把所选步骤标记为已开工'])
def test_execution_requests_are_rejected_without_generation(mes,question):
    h,g=mes
    with TestClient(h.c.app) as c:
        body=c.post('/api/manufacturing/qa',json=request(h,question=question)).json()
        assert body['response_type']=='refuse' and not body['answerable'] and not h.generation.calls


def test_unsupported_or_mixed_hierarchies_and_forged_context_rejected(mes):
    h,g=mes
    with TestClient(h.c.app) as c:
        for changes in ({'batch_id':None},{'operation_id':'ROW-A'},{'actual_started_at':'2026-10-03'},{'batch_id':'2'},{'step_id':'21'}):
            response=c.post('/api/manufacturing/qa',json=request(h,**changes))
            assert response.status_code in (404,422),response.text
        value=c.get('/api/manufacturing/work-orders/1/context?batch_id=1&step_id=11').json()
        assert value['batch']['batch_id']=='1' and value['step']['step_id']=='11'
    with pytest.raises(ValidationError):ManufacturingExecutionQuestion(**request(h,operation_id='ROW-A'))


@pytest.mark.parametrize('key,value',[('source_entity','Work Order'),('source_record_id','1'),('batch_id','2'),('value','DONE')])
def test_final_citation_validation_rejects_forged_entity_record_selection_or_value(mes,key,value):
    h,g=mes;h.generation.field='step_status';captured=[];h.generation.hook=captured.append
    with TestClient(h.c.app) as c:
        body=c.post('/api/manufacturing/qa',json=request(h,question='当前步骤的状态是什么？')).json()
        assert body['answerable']
    refs=deepcopy(body['business_citations']);refs[0][key]=value
    result=AnswerResult(answer=body['answer'],answerable=True,citations=[],business_citations=refs)
    with pytest.raises(MESError):verified_business_citations(result,captured[0].context,captured[0].decision)
