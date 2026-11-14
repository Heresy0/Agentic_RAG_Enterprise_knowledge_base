"""Business contracts over exact, authorized fields and source roles; no models."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from test_document_upload_api import api_context
from test_manufacturing_applicability import catalog
from test_manufacturing_m3_validation import evidence
from test_manufacturing_source_units import built_source
from test_manufacturing_server_bindings import draft_for
from test_erpnext_execution import ERPGraph, run_graph, scope, execution_harness
from test_manufacturing_m3_api import harness
from backend.app.manufacturing.answer_context import (ManufacturingAnswerContext, manufacturing_answer_context,
    requested_execution_fields, business_citation)
from backend.app.manufacturing.generation_models import (ExecutionManufacturingGenerationDraft,
    compile_applicability_bindings, precision_projection)
from backend.app.manufacturing.source_statements import execution_boundary_verdict
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.answer_validation import AnswerValidator


@pytest.fixture
def execution_active():
    selected = []
    async def scenario(connector):
        selected.append(await connector.read_context('MFG-WO-2026-00001', scope=scope(), operation_id='ROW-B', job_card_id='JC-B'))
    run_graph(ERPGraph(), scenario)
    return ManufacturingAnswerContext(selected[0], SimpleNamespace(state='unknown'), facts_only=True)


@pytest.mark.parametrize('question,fields', [
    ('当前工单编号、产品型号、所选工序和作业卡编号分别是什么？', ('work_order_id','product_model','operation_code','job_card_id')),
    ('请分别列出工单、所选工序和所选作业卡的实际开工时间。', ('actual_started_at','operation_actual_started_at','job_card_actual_started_at')),
    ('请分别列出所选作业卡编号、登记状态和实际开工时间。', ('job_card_id','job_card_status','job_card_actual_started_at')),
    ('所选工序的工序代码、登记状态和实际开工时间分别是什么？', ('operation_code','operation_status','operation_actual_started_at')),
    ('工单状态和作业卡状态分别是什么？', ('status','job_card_status')),
    ('工序记录编号是什么？', ('operation_id',)),
    ('工序代码是什么？', ('operation_code',)),
    ('工序编号是什么？', ()),
    ('工单记录编号是什么？', ('source_record_id',)),
    ('所选工序的状态为什么异常？', ()),
    ('工单工序的状态是什么？', ()),
    ('所选工序开工以后需要什么？', ()),
    ('所选OP-20工序状态、实际开工和精度分别是什么？', ('operation_status','operation_actual_started_at','operation_start_precision')),
    ('OP-20下所选作业卡的编号、状态和实际开工是什么？', ('job_card_id','job_card_status','job_card_actual_started_at')),
    ('所选卡的编号、状态和实际开工是什么？', ('job_card_id','job_card_status','job_card_actual_started_at')),
    ('所选OP-30工序状态、实际开工和精度分别是什么？', ()),
    ('OP-30下所选作业卡的编号、状态和实际开工是什么？', ()),
    ('所选工序的精度是什么？', ()),
    ('工单实际开工和所选工序精度是什么？', ()),
])
def test_explicit_field_lists_resolve_entity_and_attributes_without_guessing(execution_active, question, fields):
    with manufacturing_answer_context(execution_active):
        assert requested_execution_fields(question) == fields


def test_card_alias_needs_an_actual_selected_card(execution_active):
    from backend.app.manufacturing.answer_context import execution_question_text
    question='所选卡已有开工，能否认定首件检验和质量签署完成？'
    assert execution_question_text(question) == question
    record=execution_active.context.record
    execution=record.execution.model_copy(update={'job_card_id':None})
    context=execution_active.context.model_copy(update={'record':record.model_copy(update={'execution':execution})})
    with manufacturing_answer_context(replace(execution_active,context=context)):
        assert execution_question_text(question) == question


def test_wrong_parent_fields_fail_even_when_true_and_coverage_says_answered(execution_active):
    question = '所选工序的工序代码、登记状态和实际开工时间分别是什么？'
    with manufacturing_answer_context(execution_active):
        built = ContextBuilder().build([], question=question)
        original = ExecutionManufacturingGenerationDraft.model_validate(draft_for(*[
            dict(claim_kind='business_fact',business_field=field) for field in ['operation_code','status','actual_started_at']]).model_dump())
        wrong = compile_applicability_bindings(original, built)
        validation = AnswerValidator().validate(draft=wrong, context=built, question=question)
        assert not validation.valid and any('operation_status' in error for error in validation.errors)
        assert any('operation_actual_started_at' in error for error in validation.errors)
        projected = compile_applicability_bindings(precision_projection(question, built), built)
        assert AnswerValidator().validate(draft=projected, context=built, question=question).valid
        result = AnswerService.__new__(AnswerService)._render_result(draft=projected,context=built,question=question)
        assert 'Pending' in result.answer and '未知' in result.answer
        assert {row['field_name'] for row in result.business_citations} == {'operation_code','operation_status','operation_actual_started_at','operation_start_precision'}


def test_http_operation_fields_project_without_model_and_keep_three_source_checks(execution_harness):
    h = execution_harness
    response = h.client.post('/api/manufacturing/qa', json=dict(work_order_id='WO-017',operation_id='ROW-B',job_card_id='JC-B',
        question='所选工序的工序代码、登记状态和实际开工时间分别是什么？'))
    assert response.status_code == 200 and response.json()['answerable'], response.text
    assert 'Pending' in response.json()['answer'] and '未知' in response.json()['answer']
    assert len(h.graph.calls) == 9 and not h.generation.calls
    snapshot = h.client.get('/api/qa/runs/' + response.json()['run_id']).json()
    assert snapshot['snapshot']['manufacturing']['validation']['mes_reads'] == 3


@pytest.mark.parametrize('question,fields,state', [
    ('所选OP-20工序状态、实际开工和精度分别是什么？',
     {'operation_status','operation_actual_started_at','operation_start_precision'}, 'Pending'),
    ('OP-20下所选作业卡的编号、状态和实际开工是什么？',
     {'job_card_id','job_card_status','job_card_actual_started_at','job_card_start_precision'}, 'Open'),
])
def test_selected_codes_and_unknown_start_are_fields_not_overall_refusals(execution_harness,question,fields,state):
    h = execution_harness
    response = h.client.post('/api/manufacturing/qa', json=dict(work_order_id='WO-017',operation_id='ROW-B',job_card_id='JC-B',question=question))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['answerable'] and state in body['answer'] and '未知' in body['answer']
    citations = {row['field_name']:row for row in body['business_citations']}
    assert set(citations) == fields
    start = next(row for name,row in citations.items() if name.endswith('actual_started_at'))
    assert start['value'] is None
    assert start['source_record_id'] == ('ROW-B' if 'operation_status' in fields else 'JC-B')
    assert len(h.graph.calls) == 9 and not h.generation.calls
    snapshot = h.client.get('/api/qa/runs/' + body['run_id']).json()
    assert snapshot['snapshot']['manufacturing']['validation']['mes_reads'] == 3


def test_exact_legacy_field_gets_source_role_but_extra_assertion_cannot(catalog):
    active, rows, _ = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        start = next(item for item in built.business_items if item.field_name=='actual_started_at')
        for suffix, valid in [('',True), ('，所以已检验合格',False)]:
            draft = draft_for(dict(claim_kind='work_order_evidence',text=start.content+suffix,citations=[start.citation_id]))
            compiled = compile_applicability_bindings(draft,built)
            assert AnswerValidator().validate(draft=compiled,context=built,question='工单实际开工时间？').valid is valid
            if valid: assert compiled.claims[0].claim_kind=='business_fact'


def test_comparison_purpose_comes_from_trusted_mode_and_instruction_metadata(catalog):
    active, rows, _ = evidence(catalog,comparison=True)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        guides = [item for item in built.items if item.document_role=='instruction']
        draft = draft_for(*[dict(claim_kind='work_requirement',text=item.content,citations=[item.citation_id]) for item in guides])
        compiled = compile_applicability_bindings(draft,built)
        assert all(claim.claim_kind=='version_comparison' for claim in compiled.claims)
        assert AnswerValidator().validate(draft=compiled,context=built,question='两版指导书有什么差异？').valid
    active, built, citation = built_source(catalog,'作业要求不能冒充实际完成记录。')
    with manufacturing_answer_context(active):
        compiled = compile_applicability_bindings(draft_for(dict(claim_kind='version_comparison',source_unit=citation+'#1')),built)
        assert compiled.claims[0].claim_kind=='work_requirement'


RULE = '首件确认须连续检查3件，所有必检项目全部合格，并由质量人员签署后才能恢复后续作业。'
BOUNDARY = '这张作业卡已有实际开工登记，能否据此认定本工单已经完成首件检验和质量签署？应满足哪些作业要求？'


@pytest.mark.parametrize('question', ['开工登记是否说明首件编号为3？', '开工记录是否说明质量签署人员是谁？',
    '实际检验记录是否证明已经完成首件检验？', '这张作业卡的实际开工时间是什么？'])
def test_unrelated_queries_do_not_become_completion_boundary_questions(question):
    from backend.app.manufacturing.source_statements import execution_boundary_question
    assert not execution_boundary_question(question)


@pytest.mark.parametrize('rule,supported', [(RULE,True),
    ('每次开工连续检查2件，全部项目合格后由质量人员签署。',True),
    ('开工记录不能证明首件检验和质量签署已经完成。',True),
    ('报工完成不证明首件确认或质量签署已经完成。',False),
    ('首件确认须连续检查3件，所有必检项目全部合格，计划质量人员签署。',False),
    (RULE+'特殊批次无需检验。',False)])
def test_boundary_requires_independent_inspection_and_signoff_evidence(catalog,execution_active,rule,supported):
    active,built,citation = built_source(catalog,rule)
    active=replace(active,context=execution_active.context)
    built=replace(built,business_items=active.items)
    with manufacturing_answer_context(active):
        original=draft_for(dict(claim_kind='work_requirement',source_unit=citation+'#1'))
        original.coverage.append(original.coverage[0].model_copy(update=dict(requirement_id='Q2')))
        compiled=compile_applicability_bindings(original,built)
        validation=AnswerValidator().validate(draft=compiled,context=built,question=BOUNDARY)
        assert validation.valid is supported
        verdict=execution_boundary_verdict(BOUNDARY,compiled,built)
        assert (verdict is not None) is supported
        if supported:
            result=AnswerService.__new__(AnswerService)._render_result(draft=compiled,context=built,question=BOUNDARY)
            assert result.answer.startswith('仅凭实际开工登记，不能认定首件检验或质量签署已经完成')
            assert '实际未完成' not in result.answer
            assert '也不能据此判断实际是否完成' in result.answer
            cited=next(row for row in result.business_citations if row['field_name']=='job_card_actual_started_at')
            assert cited['source_record_id']=='JC-B' and cited['value'] is None
            assert any(row.get('derived_condition',{}).get('origin')=='server_execution_evidence_boundary' for row in result.claim_bindings)


AND_RULE = '''恢复后续作业前必须同时满足以下条件:
1. 适用指导书要求的首件检查全部合格。
2. 检验记录写明工单、批次和量具编号。
3. 质量人员完成签署,生产班长确认记录齐全。
4. 存在未关闭的暂停要求时,须按书面批准的恢复条件执行。'''
CARD_BOUNDARY = '所选卡已有开工并显示Completed，能否认定首件检验和质量签署完成？'


@pytest.mark.parametrize('rule,supported', [
    (AND_RULE,True),
    (AND_RULE.replace('同时满足','满足任一'),False),
    (AND_RULE.replace('3. 质量人员完成签署,生产班长确认记录齐全。\n',''),False),
    (AND_RULE.replace('质量人员完成签署','计划质量人员完成签署'),False),
    (AND_RULE + '\n5. 特殊批次无需检验。',False),
])
def test_selected_card_boundary_uses_complete_and_rule_not_status(catalog,execution_active,rule,supported):
    active,built,citation = built_source(catalog,rule)
    active=replace(active,context=execution_active.context)
    built=replace(built,business_items=active.items)
    with manufacturing_answer_context(active):
        compiled=compile_applicability_bindings(draft_for(dict(claim_kind='work_requirement',source_unit=citation+'#1')),built)
        verdict=execution_boundary_verdict(CARD_BOUNDARY,compiled,built)
        assert (verdict is not None) is supported
        if supported:
            assert compiled.claims[0].text == rule
            assert '实际是否完成' in verdict['text']
            start=next(item for item in built.business_items if item.field_name=='job_card_actual_started_at')
            assert start.citation_id in verdict['citations'] and citation in verdict['citations']
            # No metadata, truncated quotation or uncited rule may provide the premise.
            truncated=compiled.model_copy(deep=True)
            truncated.claims[0].text='1. 适用指导书要求的首件检查全部合格。\n3. 质量人员完成签署。'
            assert execution_boundary_verdict(CARD_BOUNDARY,truncated,built) is None
            assert execution_boundary_verdict(CARD_BOUNDARY,compiled,replace(built,items=[])) is None
            assert execution_boundary_verdict(CARD_BOUNDARY,compiled,replace(built,business_items=())) is None


@pytest.mark.parametrize('rule,supported', [(RULE,True), (AND_RULE,True),
    (AND_RULE.replace('同时满足','满足任一'),False),
    (AND_RULE.replace('质量人员完成签署','计划质量人员完成签署'),False),
    (RULE+'特殊批次无需检验。',False), ('只有开工登记，无检验或签署规则。',False)])
def test_grounded_boundary_derives_from_cited_complete_rule(catalog,execution_active,rule,supported):
    from backend.app.knowledge.answer_models import AnswerDraft
    from backend.app.manufacturing.generation_selection import GroundedDraft
    active,built,citation = built_source(catalog,rule)
    active=replace(active,context=execution_active.context)
    built=replace(built,business_items=active.items)
    draft=GroundedDraft.model_validate(dict(answerable=True,response_type='answer',
        claims=[dict(text='不能据开工登记认定首件检验和质量签署已完成。',citations=[citation])],
        coverage=[dict(requirement_id='Q1',aspect='开工与检验签署边界',status='answered',claim_indices=[1],missing_information=None)]))
    with manufacturing_answer_context(active):
        verdict=execution_boundary_verdict(CARD_BOUNDARY,draft,built)
        assert (verdict is not None) is supported
        assert draft.claims[0].claim_kind is None
        if supported:
            assert AnswerValidator().validate(draft=draft,context=built,question=CARD_BOUNDARY).valid
            result=AnswerService._render_result(None,draft=draft,context=built,question=CARD_BOUNDARY)
            assert result.answer.startswith('仅凭实际开工登记，不能认定')
            assert '也不能据此判断实际是否完成' in result.answer
            assert result.claim_bindings[0]['derived_condition']['origin']=='server_execution_evidence_boundary'
            start=next(x for x in built.business_items if x.field_name=='job_card_actual_started_at')
            assert set(verdict['citations'])=={start.citation_id,citation}
            # The source must be cited, current, and a rule rather than a notice.
            uncited=draft.model_copy(deep=True);uncited.claims[0].citations=[start.citation_id]
            assert execution_boundary_verdict(CARD_BOUNDARY,uncited,built) is None
            for changes in ({'business_revision':'not-current'}, {'document_role':'change_notice'}):
                altered=replace(built,items=[replace(x,**changes) if x.citation_id==citation else x for x in built.items])
                assert execution_boundary_verdict(CARD_BOUNDARY,draft,altered) is None
            # Existing untyped legacy assertions retain the old strict contract.
            legacy=AnswerDraft.model_validate(draft.model_dump())
            assert execution_boundary_verdict(CARD_BOUNDARY,legacy,built) is None


@pytest.mark.parametrize('refs', [(), ('A','B')])
@pytest.mark.parametrize('bad_value', [None, '', ['OTHER'], ['B','A']])
def test_execution_v2_only_normalizes_array_representation(execution_active,bad_value,refs):
    from backend.app.evaluation.manufacturing_execution_metrics import score_execution,LEGACY_SCORER_VERSION
    from backend.app.evaluation.manufacturing_metrics import score_manufacturing
    ctx=execution_active.context
    ctx=ctx.model_copy(update={'record':ctx.record.model_copy(update={'change_notice_refs':refs})})
    ctx=ctx.model_copy(update={'payload_sha256':ctx.record.payload_sha256})
    item=next(item for item in ManufacturingAnswerContext(ctx,None).items if item.field_name=='change_notice_refs')
    case=dict(answerable=True,business_context_binding=dict(context=ctx.model_dump(mode='json')),
        applicability_binding=dict(applicability=None),expected_business_evidence=[])
    response=dict(answerable=True,answer='演示字段',citations=[],work_order_context=ctx.record.model_dump(mode='json',exclude={'tenant_id','schema_version'}),
        business_citations=[business_citation(item,ctx)],applicability=None)
    response['work_order_context']['payload_sha256']=ctx.payload_sha256
    response=json.loads(json.dumps(response))
    assert score_execution(case,response)['automatic_proxy_pass']
    assert not score_execution(case,response,scorer_version=LEGACY_SCORER_VERSION)['automatic_proxy_pass']
    assert score_manufacturing(case,response)['manufacturing_scorer']==LEGACY_SCORER_VERSION
    changed=deepcopy(response);changed['business_citations'][0]['value']=bad_value
    assert not score_execution(case,changed)['automatic_proxy_pass']
    changed=deepcopy(response);changed['business_citations'][0]['source_record_id']='OTHER'
    assert not score_execution(case,changed)['automatic_proxy_pass']
