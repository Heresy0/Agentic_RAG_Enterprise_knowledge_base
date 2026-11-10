"""Business review -> immutable samples -> shared manufacturing Worker. No paid calls."""
import asyncio
from copy import deepcopy
import json
from uuid import UUID, uuid4
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, select

from test_document_upload_api import api_context
from manufacturing_m3_helpers import configure, qa_input, TEST_SECRET
from backend.app.api import quality, verification
from backend.app.db.models import KnowledgeBase, KnowledgeBaseUserGrant, KnowledgeDocument
from backend.app.db.business_models import BusinessScopeGrant
from backend.app.db.quality_models import QARun
from backend.app.db.conversation_models import ConversationTurn
from backend.app.integrations.mes import MESConnector
from backend.app.manufacturing.contracts import WorkOrderFixtures, canonical_digest
from backend.app.manufacturing.evaluation_sources import EvaluationSources
from backend.app.model_usage import ModelCall
from backend.app.quality.evaluation_worker import QualityEvaluationWorker


@pytest.fixture
def m4(api_context, monkeypatch, tmp_path):
    h = configure(api_context, monkeypatch)
    h.c.app.include_router(quality.router); h.c.app.include_router(verification.router)
    with h.c.session_factory() as session:
        grant = session.scalar(select(KnowledgeBaseUserGrant).where(KnowledgeBaseUserGrant.user_id == h.c.editor.user_id,
            KnowledgeBaseUserGrant.knowledge_base_id == h.c.knowledge_base_id))
        grant.permission = "admin"
        session.add(BusinessScopeGrant(tenant_id=h.c.tenant_id, user_id=h.c.editor.user_id,
            source_system_id="mock-mes-hc", plant_code="HC-P01", permission="review")); session.commit()
    source_file = tmp_path/'sources.json'
    source_file.write_text(json.dumps(dict(schema_version="mes-sources-v1", sources=[dict(
        tenant_id=str(h.c.tenant_id), source_system_id="mock-mes-hc", base_url="http://127.0.0.1:8010",
        allowed_plants=["HC-P01", "HC-P02"], token_env="MANUFACTURING_M4_TEST_TOKEN")])), encoding="utf-8")
    monkeypatch.setenv("MANUFACTURING_M4_TEST_TOKEN", TEST_SECRET)
    fixtures = WorkOrderFixtures.model_validate(dict(schema_version="work-order-fixtures-v1",
        fixture_version="workorder-v1", tenant_id=h.c.tenant_id, source_system_id="mock-mes-hc",
        records=list(h.source.records.values())))
    # SourceTransport preserves the actual M0 fixture version.
    from manufacturing_m2_helpers import FIXTURES
    fixtures = fixtures.model_copy(update={"fixture_version": FIXTURES.fixture_version})
    entry = dict(tenant_id=str(h.c.tenant_id), source_system_id="mock-mes-hc",
        fixture_version=fixtures.fixture_version, fixture_sha256=canonical_digest(fixtures.model_dump(mode="json")))
    registry = tmp_path/'registry.json'
    registry.write_text(json.dumps(dict(schema_version="manufacturing-evaluation-registry-v1", active=[entry])), encoding="utf-8")
    h.settings.manufacturing_sources_file = source_file
    h.settings.manufacturing_evaluation_registry = registry
    h.settings.manufacturing_evaluation_sources = EvaluationSources(h.settings,
        connector_factory=lambda config: MESConnector(config, transport=h.source))
    h.registry, h.entry = registry, entry
    original = h.generation.invoke
    def invoke(messages):
        return dict(parsed=original(messages), raw=SimpleNamespace(usage_metadata=dict(
            input_tokens=25, output_tokens=25, total_tokens=50), response_metadata={}), parsing_error=None)
    h.generation.invoke = invoke
    yield h
    asyncio.run(h.connector.aclose())


def approved(client, h, wo="WO-018", **changes):
    answer = client.post('/api/manufacturing/qa', json=qa_input(h, wo=wo))
    assert answer.status_code == 200, answer.text
    run_id = answer.json()['run_id']
    feedback = client.put(f'/api/qa/runs/{run_id}/feedback', json=dict(rating="down", reason="incomplete",
        comment="合成流程验收；非真实用户缺陷。", share_with_admins=True, allow_dataset_export=True))
    assert feedback.status_code == 200, feedback.text
    opened = client.post('/api/quality/cases', json=dict(run_id=run_id))
    assert opened.status_code == 201, opened.text
    case = opened.json()
    option = next(row for row in case['source']['business_evidence_options'] if row['field_name'] == 'product_model')
    selected = {key: option[key] for key in ('citation_id','field_name','value','source_system_id','source_record_id','source_revision','payload_sha256')}
    body = dict(expected_revision=1, review_status='confirmed', sample_status='approved', root_cause='generation',
        review_reason='逐项核对合成工单原始字段。', evaluation_question=qa_input(h)['question'], expected_answerable=True,
        expected_behavior='answer', reference_answer='工单型号为 '+str(option['value'])+'。', required_facts=[str(option['value'])], evidence=[],
        business_evidence=[selected], **changes)
    saved = client.put('/api/quality/cases/'+case['case']['id'], json=body)
    return case, body, saved


def dataset(client, case):
    response = client.post('/api/quality/datasets', json=dict(name='M4 合成测试', case_ids=[case['case']['id']]))
    assert response.status_code == 201, response.text
    return response.json()['id']


def cohort(client, h):
    result = []
    for wo in ('WO-017','WO-018'):
        case, _, response = approved(client,h,wo)
        assert response.status_code == 200, response.text
        result.append(dataset(client,case))
    return result


def job(client, h, datasets):
    queued = client.post('/api/quality/evaluations', json=dict(fixed_dataset_id=datasets[0],feedback_dataset_id=datasets[1],
        allow_model_calls=True,max_cases=2,max_model_calls=8,max_tokens=20000,max_seconds=120))
    assert queued.status_code == 201, queued.text
    worker = QualityEvaluationWorker(h.c.session_factory,h.settings,h.answer)
    assert worker.run_once()
    detail = client.get('/api/quality/evaluations/'+queued.json()['id'])
    assert detail.status_code == 200,detail.text
    return detail.json()


def test_business_only_review_export_and_worker_three_reads_per_case(m4):
    h=m4
    with TestClient(h.c.app) as client:
        datasets=cohort(client,h)
        sample=json.loads(client.get('/api/quality/datasets/'+datasets[0]+'/samples').text)
        assert sample['input_kind']=='manufacturing' and sample['expected_evidence']==[]
        assert sample['business_context_binding']['source']['fixture_version']==h.entry['fixture_version']
        h.source.reads.clear()
        detail=job(client,h,datasets)
        assert detail['status']=='succeeded', (detail['error_type'],detail['summary'])
        assert len(h.source.reads)==6
        assert len(detail['records'])==2 and all(row['proxy_pass'] for row in detail['records'])
        assert all(row['response']['business_citations'] and row['observations_complete'] for row in detail['records'])
        with h.c.session_factory() as session:
            assert len(list(session.scalars(select(QARun))))==2
            assert list(session.scalars(select(ConversationTurn)))==[]


@pytest.mark.parametrize('key,value', [('field_name','operation_code'),('value','secret-other-order'),
    ('source_record_id','other'),('source_revision','r9'),('payload_sha256','0'*64),('citation_id','资料1')])
def test_business_evidence_cannot_forge_field_value_or_source(m4,key,value):
    with TestClient(m4.c.app) as client:
        case,body,response=approved(client,m4)
        assert response.status_code==200,response.text
        body['expected_revision']=2; body['business_evidence'][0][key]=value
        denied=client.put('/api/quality/cases/'+case['case']['id'],json=body)
        assert denied.status_code==409,denied.text


@pytest.mark.parametrize('change', ['registry','source','rule','review','consent'])
def test_old_dataset_stops_on_active_fixture_source_rule_or_permission_change(m4,change):
    h=m4
    with TestClient(h.c.app) as client:
        case,_,response=approved(client,h)
        assert response.status_code==200,response.text
        identifier=dataset(client,case)
        if change=='registry':
            h.registry.write_text(json.dumps(dict(schema_version='manufacturing-evaluation-registry-v1',
                active=[dict(h.entry,fixture_version='new')])),encoding='utf-8')
        elif change=='source': h.source.records['WO-018']['source_revision']='r2'
        elif change=='consent':
            client.put('/api/qa/runs/'+case['case']['run_id']+'/feedback',json=dict(rating='up',share_with_admins=True,allow_dataset_export=False))
        else:
            with h.c.session_factory() as session:
                if change=='review': session.execute(delete(BusinessScopeGrant).where(BusinessScopeGrant.permission=='review'))
                else:
                    doc=session.get(KnowledgeDocument,next(iter(h.docs.values()))[0].id)
                    data=deepcopy(doc.metadata_json); data['manufacturing']['revision']+=1; doc.metadata_json=data
                session.commit()
        denied=client.get('/api/quality/datasets/'+identifier+'/samples')
        assert denied.status_code in (404,409),denied.text


def test_queue_claim_rechecks_business_authorization_before_model(m4):
    h=m4
    with TestClient(h.c.app) as client:
        datasets=cohort(client,h)
        queued=client.post('/api/quality/evaluations',json=dict(fixed_dataset_id=datasets[0],feedback_dataset_id=datasets[1],allow_model_calls=True))
        assert queued.status_code==201,queued.text
        calls=len(h.generation.calls)
        with h.c.session_factory() as session:
            session.execute(delete(BusinessScopeGrant).where(BusinessScopeGrant.permission=='review'));session.commit()
        assert QualityEvaluationWorker(h.c.session_factory,h.settings,h.answer).run_once()
        assert len(h.generation.calls)==calls
        assert client.get('/api/quality/evaluations/'+queued.json()['id']).status_code==404


def test_job_result_and_human_review_recheck_current_business_scope(m4):
    h=m4
    with TestClient(h.c.app) as client:
        detail=job(client,h,cohort(client,h))
        assert detail['status']=='succeeded', (detail['error_type'],detail['summary'])
        record=detail['records'][0]
        with h.c.session_factory() as session:
            session.execute(delete(BusinessScopeGrant).where(BusinessScopeGrant.permission=='review'));session.commit()
        assert client.get('/api/quality/evaluations/'+detail['id']).status_code==404
        denied=client.post(f"/api/quality/evaluations/{detail['id']}/reviews/{record['sample']['id']}",
            json=dict(response_binding=record['response_binding'],verdict='passed',reason='撤权后不能提交。'))
        assert denied.status_code==404


def test_injected_fault_config_difference_human_review_verify_and_close(m4,monkeypatch):
    # Flow/authorization test with synthetic models, not a wall-clock benchmark.
    # Keep scheduler noise from changing this test's publication policy outcome.
    from itertools import count
    import backend.app.quality.evaluation_worker as worker_module
    clock=count()
    monkeypatch.setattr(worker_module,'perf_counter',lambda:next(clock))
    h=m4
    with TestClient(h.c.app) as client:
        datasets=cohort(client,h)
        h.settings.rag_repair_enabled=False
        def fault(active):
            h.generation.field='operation_code' if active.context.record.work_order_id=='WO-018' else 'product_model'
        h.generation.hook=fault
        baseline=job(client,h,datasets)
        assert baseline['status']=='succeeded',baseline['error_type']
        assert baseline['summary']['partitions']['feedback']['proxy_passed']==0
        h.settings.rag_repair_enabled=True
        h.generation.hook=None;h.generation.field='product_model'
        candidate=job(client,h,datasets)
        assert candidate['status']=='succeeded',candidate['error_type']
        assert baseline['summary']['p95_ms']==candidate['summary']['p95_ms']==1000
        assert all(row['proxy_pass'] for row in candidate['records'])
        for record in candidate['records']:
            response=client.post(f"/api/quality/evaluations/{candidate['id']}/reviews/{record['sample']['id']}",
                json=dict(response_binding=record['response_binding'],verdict='passed',reason='本地替身故障注入流程检查，非真实缺陷收益。'))
            assert response.status_code==200,response.text
        target=next(row['sample']['id'][3:] for row in candidate['records'] if row['sample']['role']=='feedback')
        opened=client.post('/api/quality/improvements',json=dict(case_id=target,
            description='明确标记的本地模型字段故障注入与恢复；不代表真实用户改进。',
            change_ref='test-only:model-field-injection;config:rag_repair_enabled=false->true'))
        assert opened.status_code==201,opened.text
        row=opened.json()
        verified=client.post(f"/api/quality/improvements/{row['id']}/verify",json=dict(expected_revision=row['revision'],
            baseline_job_id=baseline['id'],candidate_job_id=candidate['id'],acknowledge_unknown_usage=True,
            note='模型替身用量已知、未定价；逐字段核对，本记录仅验收流程。'))
        assert verified.status_code==200,verified.text
        closed=client.post(f"/api/quality/improvements/{row['id']}/close",json=dict(
            expected_revision=verified.json()['revision'],note='本地故障注入流程核对完成，非正式模型验收。'))
        assert closed.status_code==200,closed.text
        assert closed.json()['status']=='closed'
        params=dict(baseline_job_id=baseline['id'],candidate_job_id=candidate['id'],acknowledge_unknown_usage=True)
        assert client.get('/api/quality/comparison',params=params).status_code==200
        assert client.get('/api/quality/improvements/'+row['id']).status_code==200
        with h.c.session_factory() as session:
            session.execute(delete(BusinessScopeGrant).where(BusinessScopeGrant.permission=='review'));session.commit()
        assert client.get('/api/quality/comparison',params=params).status_code==404
        assert client.get('/api/quality/improvements/'+row['id']).status_code==404
        assert client.post(f"/api/quality/improvements/{row['id']}/verify",json=dict(expected_revision=closed.json()['revision'],
            baseline_job_id=baseline['id'],candidate_job_id=candidate['id'],note='撤权后不能核验。')).status_code==404
        assert client.post(f"/api/quality/improvements/{row['id']}/close",json=dict(expected_revision=closed.json()['revision'],note='撤权后不能关闭。')).status_code==404


def test_identical_manufacturing_runtime_cannot_claim_improvement(m4):
    h=m4
    with TestClient(h.c.app) as client:
        datasets=cohort(client,h)
        first=job(client,h,datasets);second=job(client,h,datasets)
        comparison=client.get('/api/quality/comparison',params=dict(baseline_job_id=first['id'],candidate_job_id=second['id'],acknowledge_unknown_usage=True))
        assert comparison.status_code==200,comparison.text
        assert 'manufacturing_change_not_observed' in comparison.json()['failures']


def test_asgi_http_propagates_physical_budget_and_stops_before_model(m4):
    from backend.app.quality.eval_budget import EvaluationBudget,evaluation_budget
    h=m4
    limits=dict(max_cases=1,max_model_calls=0,max_tokens=32000,reservation_tokens=32000,max_seconds=30)
    budget=EvaluationBudget(limits)
    with TestClient(h.c.app) as client,evaluation_budget(budget):
        response=client.post('/api/manufacturing/qa',json=qa_input(h))
        assert response.status_code==503
        assert budget.calls==0 and budget.limited_by=='calls'
        assert h.generation.calls==[]
        with h.c.session_factory() as session:assert list(session.scalars(select(QARun)))==[]


@pytest.mark.parametrize('question',['工单实际开工日期是哪天，能给出几点几分？','实际开工时间是否精确到时分？'])
def test_date_only_clock_question_projects_known_date_and_limitation_without_model(m4,question):
    h=m4
    with TestClient(h.c.app) as client:
        response=client.post('/api/manufacturing/qa',json=qa_input(h,wo='WO-023',question=question))
        assert response.status_code==200,response.text
        body=response.json()
        assert body['answerable'] and '2026-10-06' in body['answer'] and '未提供具体时分' in body['answer']
        assert '00:00' not in body['answer']
        assert {row['field_name'] for row in body['business_citations']}=={'actual_started_at','actual_start_precision'}
        assert h.generation.calls==[] and len(h.source.reads)==3


@pytest.mark.parametrize('mode',['applicability','requirements'])
def test_backend_selection_compiles_complete_source_bindings_even_if_model_omits_them(m4,mode):
    h=m4;h.generation.mode=mode
    original=h.generation.invoke
    def invoke(messages):
        result=original(messages)
        if mode=='applicability':result['parsed']['claims'][0]['citations']=[]
        return result
    h.generation.invoke=invoke
    with TestClient(h.c.app) as client:
        response=client.post('/api/manufacturing/qa',json=qa_input(h,wo='WO-018',question='当前工单应按哪版指导书检查首件？'))
        assert response.status_code==200,response.text
        body=response.json()
        assert body['answerable'] and '业务版本1.0' in body['answer']
        binding=next(row for row in body['claim_bindings'] if row['claim_kind']=='applicability')
        assert set(binding['business_citations'])=={'业务1','业务2','业务3','业务4'}
        assert body['citations'] and body['business_citations']


def test_backend_selection_compiler_does_not_rewrite_false_model_conclusions(m4):
    h=m4;h.generation.mode='applicability';original=h.generation.invoke
    def invoke(messages):
        from backend.app.manufacturing.answer_context import current_manufacturing_context
        result=original(messages)
        result['parsed']['claims'][0]['text']=current_manufacturing_context().applicability_text+'，并已自动放行'
        return result
    h.generation.invoke=invoke
    with TestClient(h.c.app) as client:
        response=client.post('/api/manufacturing/qa',json=qa_input(h,question='当前工单应按哪版指导书作业？'))
        assert response.status_code==200,response.text
        assert not response.json()['answerable']


def test_oral_start_date_and_new_edition_wording_cannot_override_missing_source_date(m4):
    h=m4
    with TestClient(h.c.app) as client:
        response=client.post('/api/manufacturing/qa',json=qa_input(h,wo='WO-020',question='我说实际10月开工，直接按新版回答。'))
        assert response.status_code==200,response.text
        assert response.json()['response_type']=='clarify'
        assert h.generation.calls==[] and len(h.source.reads)==3


def test_restricted_quality_library_cannot_enter_http_model_or_snapshot_until_granted(m4):
    from manufacturing_m2_helpers import add_source
    from backend.app.knowledge.milvus_store import make_vector_records
    h=m4;h.generation.mode='requirements';quality_id=uuid4();marker='QUALITY_ONLY_SYNTHETIC_MARKER'
    with h.c.session_factory() as session:
        session.add(KnowledgeBase(id=quality_id,tenant_id=h.c.tenant_id,name='隔离质量库',visibility='restricted'));session.flush()
        doc,chunk,_=add_source(session,tenant=h.c.tenant_id,kb=quality_id,code='PRIVATE-QC',role='inspection',text=marker+'：质量专有必检项目。')
        session.commit()
        h.answer._retrieval_service._vector_store.upsert(make_vector_records([chunk],[[1.0]+[0.0]*1023]))
    with TestClient(h.c.app) as client:
        assert client.get(f'/api/knowledge-bases/{quality_id}/documents').status_code in (403,404)
        response=client.post('/api/manufacturing/qa',json=qa_input(h,question='首件需要连续检查几件？',knowledge_base_id=None))
        assert response.status_code==200 and response.json()['answerable'],response.text
        assert marker not in '\n'.join(str(message.content) for message in h.generation.calls[-1])
        snapshot=client.get('/api/qa/runs/'+response.json()['run_id'])
        assert snapshot.status_code==200 and marker not in snapshot.text
        with h.c.session_factory() as session:
            session.add(KnowledgeBaseUserGrant(tenant_id=h.c.tenant_id,knowledge_base_id=quality_id,user_id=h.c.editor.user_id,permission='admin'));session.commit()
        assert client.get(f'/api/knowledge-bases/{quality_id}/documents').status_code==200
        positive=client.post('/api/manufacturing/qa',json=qa_input(h,question='首件需要连续检查几件？',knowledge_base_id=None))
        assert positive.status_code==200 and positive.json()['answerable'],positive.text
        assert marker in '\n'.join(str(message.content) for message in h.generation.calls[-1])


def test_all_manufacturing_and_snapshot_entries_require_login_with_positive_control(m4):
    from fastapi import HTTPException
    from backend.app.security.dependencies import get_current_principal
    h=m4
    with TestClient(h.c.app) as client:
        answer=client.post('/api/manufacturing/qa',json=qa_input(h));assert answer.status_code==200
        created=client.post('/api/manufacturing/conversations',json=dict(work_order_id='WO-018',knowledge_base_id=str(h.c.knowledge_base_id)))
        assert created.status_code==201,created.text
        conversation=created.json()['id'];before=len(h.source.reads)
        original=h.c.app.dependency_overrides[get_current_principal]
        def anonymous():raise HTTPException(401,'synthetic login required')
        h.c.app.dependency_overrides[get_current_principal]=anonymous
        try:
            for method,path,payload in [('GET','/api/manufacturing/work-orders',None),('GET','/api/manufacturing/work-orders/WO-018/context',None),
                ('POST','/api/manufacturing/qa',qa_input(h)),('GET','/api/manufacturing/conversations',None),
                ('GET','/api/manufacturing/conversations/'+conversation,None),('POST','/api/manufacturing/conversations',dict(work_order_id='WO-018')),
                ('DELETE','/api/manufacturing/conversations/'+conversation,None),('GET','/api/qa/runs/'+answer.json()['run_id'],None)]:
                denied=client.request(method,path,json=payload)
                assert denied.status_code==401 and 'HC-A17' not in denied.text
            assert len(h.source.reads)==before and len(h.generation.calls)==1
        finally:h.c.app.dependency_overrides[get_current_principal]=original


def test_private_business_feedback_cannot_be_reviewed_until_explicitly_shared(m4):
    h=m4
    with TestClient(h.c.app) as client:
        answer=client.post('/api/manufacturing/qa',json=qa_input(h));assert answer.status_code==200
        identifier=answer.json()['run_id']
        for shared,status in [(False,404),(True,201)]:
            feedback=client.put('/api/qa/runs/'+identifier+'/feedback',json=dict(rating='up',share_with_admins=shared,allow_dataset_export=False))
            assert feedback.status_code==200,feedback.text
            review=client.post('/api/quality/cases',json=dict(run_id=identifier))
            assert review.status_code==status,review.text


def test_cross_tenant_http_cannot_use_another_tenants_connector(m4):
    from backend.app.db.models import Tenant,User
    from backend.app.security.principal import Principal
    from backend.app.security.dependencies import get_current_principal
    h=m4;tenant=uuid4();user=uuid4()
    with h.c.session_factory() as session:
        session.add(Tenant(id=tenant,name='隔离其他租户'));session.flush()
        session.add(User(id=user,tenant_id=tenant,external_subject='synthetic-other',name='other'));session.flush()
        session.add(BusinessScopeGrant(tenant_id=tenant,user_id=user,source_system_id='mock-mes-hc',plant_code='HC-P01',permission='view'));session.commit()
    original=h.c.app.dependency_overrides[get_current_principal]
    h.c.app.state.mes_connectors[tenant]=h.connector  # Even an incorrect runtime mapping cannot cross tenant.
    with TestClient(h.c.app) as client:
        h.c.app.dependency_overrides[get_current_principal]=lambda:Principal(user,tenant,'synthetic-other')
        try:
            for path in ['/api/manufacturing/work-orders','/api/manufacturing/work-orders/WO-018/context']:
                response=client.get(path)
                assert response.status_code==404 and 'HC-A17' not in response.text
            assert not h.source.reads and not h.generation.calls
        finally:h.c.app.dependency_overrides[get_current_principal]=original
        assert client.get('/api/manufacturing/work-orders').status_code==200
        assert client.get('/api/manufacturing/work-orders/WO-018/context').status_code==200


@pytest.mark.parametrize('remaining',['knowledge_only','business_only'])
def test_http_requires_business_and_document_grants_with_positive_control(m4,remaining):
    h=m4
    with TestClient(h.c.app) as client:
        with h.c.session_factory() as session:
            if remaining=='knowledge_only':session.execute(delete(BusinessScopeGrant))
            else:session.execute(delete(KnowledgeBaseUserGrant).where(KnowledgeBaseUserGrant.user_id==h.c.editor.user_id))
            session.commit()
        response=client.post('/api/manufacturing/qa',json=qa_input(h))
        assert response.status_code in (403,404) and not h.generation.calls
        assert client.get('/api/manufacturing/work-orders/WO-018/context').status_code==(404 if remaining=='knowledge_only' else 200)
        with h.c.session_factory() as session:
            if remaining=='knowledge_only':session.add(BusinessScopeGrant(tenant_id=h.c.tenant_id,user_id=h.c.editor.user_id,source_system_id='mock-mes-hc',plant_code='HC-P01',permission='view'))
            else:session.add(KnowledgeBaseUserGrant(tenant_id=h.c.tenant_id,knowledge_base_id=h.c.knowledge_base_id,user_id=h.c.editor.user_id,permission='admin'))
            session.commit()
        assert client.post('/api/manufacturing/qa',json=qa_input(h)).status_code==200


def test_dataset_requires_explicit_export_consent_before_creation(m4):
    with TestClient(m4.c.app) as client:
        case,body,response=approved(client,m4)
        assert response.status_code==200
        path='/api/qa/runs/'+case['case']['run_id']+'/feedback'
        for consent,status in [(False,409),(True,201)]:
            response=client.put(path,json=dict(rating='down',reason='incomplete',share_with_admins=True,allow_dataset_export=consent))
            assert response.status_code==200
            # Updating feedback invalidates the prior review, so only the
            # missing-consent branch is attempted before a fresh approval.
            if consent:
                detail=client.get('/api/quality/cases/'+case['case']['id']).json()
                body.update(expected_revision=detail['case']['revision'],sample_status='approved')
                reviewed=client.put('/api/quality/cases/'+case['case']['id'],json=body)
                assert reviewed.status_code==200,reviewed.text
            exported=client.post('/api/quality/datasets',json=dict(name='显式授权对照',case_ids=[case['case']['id']]))
            assert exported.status_code==status,exported.text


def test_quantity_followup_keeps_bound_order_and_old_edition(m4):
    h=m4;h.generation.mode='applicability'
    with TestClient(h.c.app) as client:
        created=client.post('/api/manufacturing/conversations',json=dict(work_order_id='WO-018',knowledge_base_id=str(h.c.knowledge_base_id)))
        assert created.status_code==201
        identifier=created.json()['id']
        first=client.post('/api/manufacturing/qa',json=qa_input(h,conversation_id=identifier,question='应使用哪版指导书？'))
        assert first.status_code==200 and first.json()['applicability']['business_revision']=='1.0'
        h.generation.mode='requirements'
        followup=client.post('/api/manufacturing/qa',json=qa_input(h,conversation_id=identifier,question='那需要连续检几件？'))
        assert followup.status_code==200 and followup.json()['answerable'],followup.text
        body=followup.json()
        assert body['conversation_id']==identifier and body['work_order_context']['work_order_id']=='WO-018'
        assert body['applicability']['business_revision']=='1.0' and '连续两件' in body['answer']
        assert len(h.source.reads)==7  # One creation read plus three reads for each answer.
        assert all(row['work_order_id']=='WO-018' for row in body['business_citations'])
