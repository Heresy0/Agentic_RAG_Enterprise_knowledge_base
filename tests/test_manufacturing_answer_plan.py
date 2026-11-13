"""Field authority, explanatory prose and repair have separate responsibilities."""
from dataclasses import replace

import pytest
from pydantic import ValidationError

from test_document_upload_api import api_context
from test_manufacturing_applicability import catalog
from test_manufacturing_m3_validation import evidence
from test_manufacturing_source_units import built_source
from test_manufacturing_answer_contracts import execution_active, AND_RULE
from test_manufacturing_selection_contract import payload, grounded_payload
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.output_contract import live_generation_schema, prepare_draft
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.answer_repair import preserves_facts
from backend.app.knowledge.answer_models import AnswerClaim, AnswerDraft, EvidenceFirstAnswerDraft
from backend.app.manufacturing.answer_context import manufacturing_answer_context, unknown_start_note
from backend.app.manufacturing.generation_selection import answer_sources, GroundedDraft


def test_business_sources_compile_exact_values_and_cannot_carry_a_parallel_assertion(catalog):
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        source = context.business_items[1]
        schema = live_generation_schema(context)
        draft = prepare_draft(schema.model_validate(payload(source.citation_id)), context)
        assert draft.claims[0].text == source.content
        assert draft.claims[0].claim_kind == 'business_fact'
        assert AnswerValidator().validate(draft=draft, context=context, question='产品型号？').valid
        for assertion in ({'text':'已执行'},{'citations':['业务1']},{'claim_kind':'applicability'}):
            data=payload(source.citation_id);data['claims'][0].update(assertion)
            with pytest.raises(ValidationError):schema.model_validate(data)
        with pytest.raises(ValidationError):schema.model_validate(grounded_payload('猜测字段值', source.citation_id))


def test_source_plan_rebinds_only_current_authorized_items(catalog):
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context=ContextBuilder().build([])
        wire=live_generation_schema(context).model_validate(payload('业务2'))
        with pytest.raises(ValidationError):prepare_draft(wire, replace(context,business_items=context.business_items[:1]))
        changed=replace(context,business_items=tuple(replace(x,content='当前授权型号是PART-Z77',value='PART-Z77')
                                                  if x.citation_id=='业务2' else x for x in context.business_items))
        result=prepare_draft(wire,changed)
        assert result.claims[0].text=='当前授权型号是PART-Z77'
        assert active.context.record.product_model not in result.claims[0].text


def test_document_explanations_cannot_rewrite_business_fields_or_invent_refs(catalog):
    active, context, citation=built_source(catalog,'开工记录不能证明检验完成。')
    with manufacturing_answer_context(active):
        schema=live_generation_schema(context)
        wire=schema.model_validate(grounded_payload('不能凭开工记录确认检验完成。',citation))
        assert isinstance(prepare_draft(wire,context),GroundedDraft)
        for ref in ('业务2','资料999'):
            with pytest.raises(ValidationError):schema.model_validate(grounded_payload('猜测执行事实。',ref))


def test_backend_applicability_and_document_prose_share_one_plan(catalog):
    active, context, citation=built_source(catalog,'作业要求必须经质量签署。')
    with manufacturing_answer_context(active):
        assert '后端适用性' in answer_sources(context)
        data=payload('后端适用性')
        data['claims'].append(dict(text='作业要求必须经质量签署。',citations=[citation]))
        data['coverage'][0]['claim_indices']=[1,2]
        draft=prepare_draft(live_generation_schema(context).model_validate(data),context)
        assert sum(c.claim_kind=='applicability' for c in draft.claims)==1
        assert draft.claims[0].text==active.applicability_text
        assert draft.claims[1].text==data['claims'][1]['text']
        # A missing document invalidates the backend source, not just its display text.
        with pytest.raises(ValidationError):prepare_draft(live_generation_schema(context).model_validate(data),replace(context,items=[]))


@pytest.mark.parametrize('rule,available',[(AND_RULE,True),(AND_RULE.replace('同时满足','满足任一'),False),
                                         (AND_RULE+'\n5. 特殊批次免检。',False)])
def test_backend_boundary_uses_existing_complete_source_proof(catalog,execution_active,rule,available):
    active,context,citation=built_source(catalog,rule)
    active=replace(active,context=execution_active.context)
    context=replace(context,business_items=active.items)
    question='所选作业卡已有开工登记，能否证明首件检验和质量签署已完成？'
    with manufacturing_answer_context(active):
        sources=answer_sources(context,question)
        assert ('后端开工边界' in sources) is available
        if available:
            wire=live_generation_schema(context,question).model_validate(payload('后端开工边界'))
            draft=prepare_draft(wire,context,question=question)
            assert AnswerValidator().validate(draft=draft,context=context,question=question).valid
            result=AnswerService._render_result(None,draft=draft,context=context,question=question)
            assert result.answer.count('仅凭实际开工登记')==1
            assert '也不能据此判断实际是否完成' in result.answer
            assert citation in sources['后端开工边界'].citations
            unbound=replace(context,items=[replace(x,document_id='unbound-document') for x in context.items])
            assert '后端开工边界' not in answer_sources(unbound,question)


def test_unknown_start_note_is_a_source_boundary_not_an_execution_status(catalog,execution_active):
    active,context,_=built_source(catalog,'保留开工记录。')
    ctx=execution_active.context
    ctx=ctx.model_copy(update={'record':ctx.record.model_copy(update={'execution':ctx.record.execution.model_copy(
        update={'job_card_actual_started_at':None,'job_card_start_precision':None})})})
    active=replace(active,context=ctx,facts_only=True)
    context=replace(context,items=[],business_items=active.items)
    with manufacturing_answer_context(active):
        source=next(x for x in context.business_items if x.field_name=='job_card_actual_started_at')
        draft=prepare_draft(live_generation_schema(context).model_validate(payload(source.citation_id)),context)
        result=AnswerService._render_result(None,draft=draft,context=context,question='本卡开工登记是什么？')
        assert source.content in result.answer and '不能据此确认已开工或认定实际未开工' in result.answer
        assert '不能用其他实体或计划时间补值' in result.answer
        assert result.business_citations[0]['value'] is None
        assert result.claim_bindings[0]['derived_condition']['origin']=='server_unknown_start_boundary'
        for item in context.business_items:
            if item.field_name!='job_card_actual_started_at':
                assert (unknown_start_note(item) is not None)==(item.value is None and item.field_name.endswith('actual_started_at'))


def test_repair_cannot_downgrade_a_compiled_fact_to_free_prose():
    original=EvidenceFirstAnswerDraft.model_validate(grounded_payload('当前值未知','业务1'))
    original.claims[0].claim_kind='business_fact'
    changed=original.model_copy(deep=True);changed.claims[0].claim_kind=None
    assert not preserves_facts(original,changed)


def test_exact_duplicate_display_does_not_drop_distinct_claims():
    context=ContextBuilder().build([])
    draft=AnswerDraft(answerable=True,claims=[AnswerClaim(text=t,citations=[]) for t in ('相同事实','相同事实','另一事实')])
    result=AnswerService._render_result(None,draft=draft,context=context)
    assert result.answer.count('相同事实')==1 and '另一事实' in result.answer
