"""Reviewed step assignment is distinct from upstream facts and start-window rules."""
from copy import deepcopy
from uuid import UUID
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,delete
from test_openmes_quality import openmes_quality,m4,api_context
from test_document_upload_api import _set_principal
from backend.app.db.business_models import StepGuidanceRevision,BusinessScopeGrant
from backend.app.db.models import KnowledgeDocument
from test_manufacturing_m4 import dataset


def path(h,wo='1',step='11'):
    return f'/api/manufacturing/work-orders/{wo}/batches/{wo}/steps/{step}/guidance-mapping?knowledge_base_id={h.c.knowledge_base_id}'


def mapping(c,h,wo='1',step='11',document='new'):
    ctx=c.get(f'/api/manufacturing/work-orders/{wo}/context?batch_id={wo}&step_id={step}').json()
    body=dict(expected_revision=0,state='draft',document_id=str(h.docs[document][0].id),operation_code='OP-20',
        expected_source_revision=ctx['source_revision'],review_note='助手非独立审核：为这个确切步骤指定指导书版本，不推定工单开工时间。')
    draft=c.put(path(h,wo,step),json=body);assert draft.status_code==200,draft.text
    body.update(expected_revision=1,state='reviewed')
    reviewed=c.put(path(h,wo,step),json=body);assert reviewed.status_code==200,reviewed.text
    return reviewed.json(),body


def ask(c,**changes):
    return c.post('/api/manufacturing/qa',json=dict(dict(work_order_id='1',batch_id='1',step_id='11',question='当前步骤应该采用哪个指导书版本？'),**changes))


def test_reviewed_step_mapping_selects_instruction_without_inventing_parent_facts(openmes_quality):
    h=openmes_quality;h.generation.mode='applicability'
    with TestClient(h.c.app) as c:
        missing=ask(c).json();assert missing['response_type']=='clarify' and '步骤指导书审核映射' in missing['missing_fields']
        saved,_=mapping(c,h)
        answer=ask(c);assert answer.status_code==200,answer.text
        body=answer.json();assert body['answerable'] and body['applicability']['business_revision']=='2.0',body
        assert body['applicability']['step_guidance']['basis']=='reviewed_step_assignment'
        assert body['work_order_context']['actual_started_at'] is None and body['work_order_context']['operation_code'] is None
        assert '步骤11经审核指定适用' in body['answer']
        assert {'work_order_id','batch_id','step_id','product_model'} <= {x['field_name'] for x in body['business_citations']}
        snapshot=c.get('/api/qa/runs/'+body['run_id']).json()['snapshot']['manufacturing']
        assert snapshot['validation']['mes_reads']==3 and snapshot['applicability']['step_guidance']['revision']==2
        info=c.get(path(h)).json();assert [x['revision'] for x in info['history']]==[2,1]
        assert ask(c,step_id='12').json()['response_type']=='clarify'


def test_mapping_requires_draft_concurrency_scope_and_explicit_code(openmes_quality):
    h=openmes_quality
    with TestClient(h.c.app) as c:
        saved,body=mapping(c,h)
        assert c.put(path(h),json=body).status_code==409
        body.update(expected_revision=2,state='draft',operation_code='OP-30')
        assert c.put(path(h),json=body).status_code==409
        body['operation_code']='OP-20';body['document_id']=str(h.docs['qc'][0].id)
        assert c.put(path(h),json=body).status_code==409
        body['document_id']=str(h.docs['new'][0].id);body['state']='reviewed'
        assert c.put(path(h),json=body).status_code==409
        _set_principal(h.c,h.c.viewer)
        assert c.get(path(h)).status_code in (403,404)


@pytest.mark.parametrize('change',['source','document','withdrawn','draft','review_permission'])
def test_mapping_rechecks_source_document_and_review_lifecycle(openmes_quality,change):
    h=openmes_quality;h.generation.mode='applicability'
    with TestClient(h.c.app) as c:
        saved,body=mapping(c,h)
        if change=='source':h.graph.batches['1']['steps'][0]['status']='DONE'
        elif change=='document':
            with h.c.session_factory() as db:
                doc=db.get(KnowledgeDocument,h.docs['new'][0].id);doc.metadata_json={**doc.metadata_json,'publication':{'state':'draft','revision':2}};db.commit()
        elif change=='review_permission':
            with h.c.session_factory() as db:db.execute(delete(BusinessScopeGrant).where(BusinessScopeGrant.permission=='review'));db.commit()
            assert c.put(path(h),json=dict(expected_revision=2,state='withdrawn',review_note='撤回')).status_code==404
            # Reviewer role controls editing; active readers may still use an approved mapping.
            assert ask(c).json()['answerable']
            return
        else:
            body.update(expected_revision=2,state=change)
            assert c.put(path(h),json=body).status_code==200
        response=ask(c);assert response.status_code==200,response.text
        assert response.json()['response_type']=='clarify' and not h.generation.calls


def test_mapping_change_during_generation_blocks_delivery(openmes_quality):
    h=openmes_quality;h.generation.mode='applicability'
    with TestClient(h.c.app) as c:
        mapping(c,h)
        def changed(active):
            with h.c.session_factory() as db:
                row=db.scalar(select(StepGuidanceRevision).where(StepGuidanceRevision.revision==2));row.state='withdrawn';db.commit()
        h.generation.hook=changed
        assert ask(c).status_code==409


def test_mapping_change_invalidates_existing_conversation_before_followup_model(openmes_quality):
    h=openmes_quality;h.generation.mode='applicability'
    with TestClient(h.c.app) as c:
        _,body=mapping(c,h)
        created=c.post('/api/manufacturing/conversations',json=dict(work_order_id='1',batch_id='1',step_id='11'))
        assert created.status_code==201,created.text
        identifier=created.json()['id'];assert ask(c,conversation_id=identifier).status_code==200
        before=len(h.generation.calls)
        assert c.put(path(h),json=dict(expected_revision=2,state='withdrawn',review_note='撤回旧步骤分配')).status_code==200
        assert ask(c,conversation_id=identifier).status_code==409
        assert len(h.generation.calls)==before


def test_mapped_guidance_sample_freezes_assignment_and_invalidates_when_withdrawn(openmes_quality):
    h=openmes_quality;h.generation.mode='applicability'
    with TestClient(h.c.app) as c:
        mapping(c,h)
        answer=ask(c).json();run=answer['run_id']
        assert c.put('/api/qa/runs/'+run+'/feedback',json=dict(rating='up',share_with_admins=True,allow_dataset_export=True)).status_code==200
        opened=c.post('/api/quality/cases',json=dict(run_id=run));assert opened.status_code==201,opened.text
        case=opened.json();keys=['citation_id','field_name','value','source_system_id','source_record_id','source_revision','payload_sha256','source_entity','batch_id','step_id','actual_start_precision']
        business=[{k:x[k] for k in keys if k in x} for x in case['source']['business_evidence_options'] if x['field_name'] in ('work_order_id','product_model','batch_id','step_id')]
        evidence=[dict(citation_id=x['citation_id'],quote=x['content']) for x in case['source']['snapshot']['context']['items']]
        saved=c.put('/api/quality/cases/'+case['case']['id'],json=dict(expected_revision=1,review_status='confirmed',sample_status='approved',root_cause='other',
            review_reason='助手非独立审核步骤映射与指导书原文。',evaluation_question='当前步骤应该采用哪个指导书版本？',expected_answerable=True,expected_behavior='answer',
            reference_answer='当前步骤经审核指定适用2.0。',required_facts=['2.0'],business_evidence=business,evidence=evidence))
        assert saved.status_code==200,saved.text
        version=dataset(c,case);sample=c.get('/api/quality/datasets/'+version+'/samples');assert sample.status_code==200,sample.text
        assert 'reviewed_step_assignment' in sample.text
        assert c.put(path(h),json=dict(expected_revision=2,state='withdrawn',review_note='撤回映射')).status_code==200
        assert c.get('/api/quality/datasets/'+version+'/samples').status_code==409


def test_explicit_written_order_exception_conflicts_with_step_assignment(openmes_quality):
    from manufacturing_m2_helpers import add_source
    h=openmes_quality
    with TestClient(h.c.app) as c:
        mapping(c,h)
        with h.c.session_factory() as db:
            add_source(db,tenant=h.c.tenant_id,kb=h.c.knowledge_base_id,code='MES-EXCEPTION',role='change_notice',work_orders=('1',),
                text='工单1明确执行1.0。',rules=(dict(rule_id='MES-exception',kind='work_order_override',target_document_code='HC-WI-A17-020',
                    target_business_revision='1.0',product_model='HC-A17',operation_code='OP-20',work_order_ids=('1',),effective_from='2026-01-01'),))
            db.commit()
        response=ask(c);assert response.status_code==200,response.text
        assert response.json()['applicability']['state']=='conflict' and not h.generation.calls
