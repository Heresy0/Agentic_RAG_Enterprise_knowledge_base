import pytest

from backend.app.evaluation.metrics import score_answer
from backend.app.knowledge.answer_coverage import coverage_snapshot
from backend.app.knowledge.answer_models import AnswerCoverage, EvidenceFirstAnswerDraft
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.rag_budget import RagBudget, rag_budget_scope
from backend.app.evaluation.trace import capture_trace
from test_answer_service import make_document, build_service, answer
from test_answer_repair import repair_service


TEXT = '设备在室内稳定30min，待温度平衡后进行校准。'


def draft(quote=TEXT, *, text='设备需在室内稳定30min，待温度平衡后进行校准', citations=None):
    return EvidenceFirstAnswerDraft.model_validate(dict(answerable=True, response_type='answer',
        claims=[dict(text=text, citations=citations or ['资料1'])], coverage=[dict(
            requirement_id='Q1', aspect='校准前置条件', status='answered', claim_indices=[1],
            missing_information=None, source_quote=quote)]))


def context(*contents):
    return ContextBuilder().build([make_document(text, name=f'规范{i}', chunk_id=f'c{i}')
                                   for i, text in enumerate(contents, 1)])


def validate(model, ctx=None):
    return AnswerValidator().validate(draft=model, context=ctx or context(TEXT), question='校准前需要什么条件？')


def test_evidence_is_emitted_before_claims_but_quote_is_optional_in_live_schema():
    schema = EvidenceFirstAnswerDraft.model_json_schema()
    assert list(schema['properties']).index('coverage') < list(schema['properties']).index('claims')
    assert 'source_quote' not in schema['$defs']['EvidenceCoverage']['required']
    assert list(schema['$defs']['EvidenceCoverage']['properties']).index('source_quote') < list(schema['$defs']['EvidenceCoverage']['properties']).index('aspect')
    payload = draft().model_dump()
    del payload['coverage'][0]['source_quote']
    assert EvidenceFirstAnswerDraft.model_validate(payload).coverage[0].source_quote is None


@pytest.mark.parametrize('quote', [None, '', ' \n '])
def test_missing_quote_is_diagnostic_only(quote):
    result = validate(draft(quote))
    assert result.valid
    saved = coverage_snapshot(draft(quote), '校准前需要什么条件？', context(TEXT))
    assert saved['structurally_complete'] and not saved['errors']
    assert any('缺少原文摘录' in warning for warning in saved['quote_warnings'])


@pytest.mark.parametrize('quote', ['设备稳定20min后校准。', '设备稳定30min，不必等待温度平衡。',
                                  '设备在室内稳定30min，进行校准。'])
def test_changed_quote_does_not_reject_independently_valid_claims(quote):
    result = validate(draft(quote))
    assert result.valid
    saved = coverage_snapshot(draft(quote), '校准前需要什么条件？', context(TEXT))
    assert any('原文摘录不在对应引用资料中' in warning for warning in saved['quote_warnings'])


@pytest.mark.parametrize('quote', [30, ['摘录'], {'text': TEXT}, TEXT * 30])
def test_malformed_auxiliary_quote_cannot_cause_a_schema_refusal(quote):
    model = draft(quote)
    assert model.coverage[0].source_quote is None and validate(model).valid


def test_quote_diagnostic_ignores_layout_whitespace():
    ctx = context('设备在室内稳定 30min，待温度\n平衡后进行校准。')
    assert not coverage_snapshot(draft(), '校准前需要什么条件？', ctx)['quote_warnings']


def test_quote_in_an_uncited_document_remains_a_warning():
    ctx = context(TEXT, '本设备无须等待温度平衡。')
    result = validate(draft('本设备无须等待温度平衡。'), ctx)
    assert result.valid
    assert coverage_snapshot(draft('本设备无须等待温度平衡。'), '校准前需要什么条件？', ctx)['quote_warnings']


@pytest.mark.parametrize('model,marker', [
    (draft('无效摘录', text='设备需稳定20min'), '数字或日期缺少证据支持'),
    (draft('无效摘录', citations=['资料999']), '不存在的引用'),
])
def test_removing_quote_gate_keeps_claim_and_citation_checks(model, marker):
    result = validate(model)
    assert not result.valid and any(marker in e for e in result.errors)


def test_removing_quote_gate_keeps_required_point_checks():
    model = draft(None)
    model.coverage[0].claim_indices = []
    assert not validate(model).valid


@pytest.mark.parametrize('quote', [None, '改写后的摘录', '设备稳定20min后校准。'])
def test_quote_warning_does_not_trigger_repair_or_final_refusal(monkeypatch, quote):
    service, _, llm, *_ = build_service(monkeypatch,
        documents=[make_document(TEXT)], model_result=draft(quote))
    service._answer_repair, repair_calls, _ = repair_service(RuntimeError('must not call'))
    with rag_budget_scope(RagBudget()), capture_trace() as trace:
        result = answer(service, '校准前需要什么条件？')
    assert result.answerable and result.response_type == 'answer'
    assert '温度平衡' in result.answer and result.citations
    assert len(llm.calls) == 1 and not repair_calls
    assert trace.stages['answer_generation'][0]['coverage']['quote_warnings']


def test_trace_retains_the_selected_quote_without_claiming_semantic_completeness():
    model = draft(text='设备需在室内稳定30min')
    assert validate(model).valid  # Quote authenticity does not prove all conditions were answered.
    saved = coverage_snapshot(model, '校准前需要什么条件？', context(TEXT))
    assert saved['coverage'][0]['source_quote'] == TEXT
    metrics = score_answer(dict(answerable=True, required_facts=['30min', '温度平衡']),
                           dict(answer='设备需在室内稳定30min', answerable=True, citations=[]))
    assert metrics['missing_facts'] == ['温度平衡']


def test_historical_coverage_remains_readable_without_new_quote_field():
    row = AnswerCoverage.model_validate(dict(requirement_id='Q1', aspect='历史要点',
        status='answered', claim_indices=[1], missing_information=None))
    assert row.status == 'answered'


@pytest.mark.parametrize('response_type', ['clarify', 'refuse'])
def test_unused_quote_does_not_convert_a_nonanswer_into_a_protocol_failure(response_type):
    model = EvidenceFirstAnswerDraft.model_validate(dict(answerable=False, response_type=response_type,
        missing_fields=['对象'] if response_type=='clarify' else [],
        clarification_questions=['请说明对象。'] if response_type=='clarify' else [],
        coverage=[dict(requirement_id='Q1', aspect='对象未确定', status='insufficient',
            claim_indices=[], missing_information='缺少对象', source_quote='模型多余的未回答要点摘录')]))
    normalized = AnswerService._as_generation_draft(model)
    assert normalized.response_type == response_type and normalized.coverage[0].source_quote is None
    assert model.coverage[0].source_quote is not None
    assert validate(normalized).valid
