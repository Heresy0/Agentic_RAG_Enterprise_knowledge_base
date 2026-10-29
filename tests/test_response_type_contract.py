"""Live generation is strict; historical answer payloads remain readable."""
import pytest
from pydantic import ValidationError

from backend.app.evaluation.trace import capture_trace
from backend.app.knowledge.answer_models import AnswerDraft, AnswerResult, GenerationAnswerDraft
from test_answer_service import answer, build_service, make_document
from test_rag_quality_fixes import schema_service


def payload(**changes):
    return dict(answerable=False, claims=[], coverage=[dict(
        requirement_id='Q1', aspect='设备条件', status='insufficient', claim_indices=[],
        missing_information='缺少设备型号')], **changes)


def test_generation_schema_requires_non_nullable_response_type():
    schema = GenerationAnswerDraft.model_json_schema()
    assert 'response_type' in schema['required']
    assert schema['properties']['response_type']['type'] == 'string'
    assert schema['properties']['response_type']['enum'] == ['answer', 'clarify', 'refuse']


@pytest.mark.parametrize('changes,error_type', [({}, 'missing'), ({'response_type': None}, 'literal_error')])
def test_new_generation_rejects_missing_or_null_type(changes, error_type):
    with pytest.raises(ValidationError) as exc:
        GenerationAnswerDraft.model_validate(payload(**changes))
    assert [(error['loc'], error['type']) for error in exc.value.errors()] == [
        (('response_type',), error_type)]


@pytest.mark.parametrize('response_type', ['answer', 'clarify', 'refuse'])
def test_explicit_response_types_are_accepted(response_type):
    assert GenerationAnswerDraft.model_validate(payload(response_type=response_type)).response_type == response_type


@pytest.mark.parametrize('changes', [{}, {'response_type': None}])
def test_missing_type_never_reaches_business_refusal_without_repair(monkeypatch, changes):
    service, _, model, *_ = build_service(monkeypatch, model_result=None,
                                         documents=[make_document('设备需先确认型号。')])
    model.invoke = lambda messages: payload(**changes)
    with pytest.raises(ValidationError) as exc:
        answer(service, '设备需要什么条件？')
    assert exc.value.errors()[0]['loc'] == ('response_type',)


@pytest.mark.parametrize('changes', [{}, {'response_type': None}])
def test_missing_type_uses_existing_explicit_protocol_failure_path(monkeypatch, changes):
    service, calls, _ = schema_service(monkeypatch, candidate=RuntimeError('repair unavailable'))
    service._structured_llm.invoke = lambda messages: payload(**changes)
    with capture_trace() as trace:
        result = answer(service, '设备需要什么条件？')
    assert not result.answerable and not result.citations and len(calls) == 1
    assert result.refusal_reason == '模型输出格式异常，未形成可验证答案，请重试。'
    event = next(event for event in trace.stages['rag_routing'] if event['kind'] == 'schema_error')
    assert event['errors'][0]['location'] == ['response_type']
    assert any(decision['reason'] == 'structured_output_invalid'
               for decision in trace.stages['answer_decisions'])


def test_missing_type_can_be_recovered_by_existing_single_schema_repair(monkeypatch):
    service, calls, _ = schema_service(monkeypatch)
    service._structured_llm.invoke = lambda messages: payload()
    with capture_trace() as trace:
        result = answer(service, '温度是多少？')
    assert result.answerable and result.response_type == 'answer' and len(calls) == 1
    event = next(event for event in trace.stages['rag_routing'] if event['kind'] == 'schema_error')
    assert event['errors'] == [{'location': ['response_type'], 'type': 'missing'}]


def test_historical_drafts_and_results_still_accept_missing_response_type():
    assert not AnswerDraft.model_validate({'answerable': False, 'claims': []}).answerable
    result = AnswerResult.model_validate({'answer': '历史回答', 'answerable': False, 'citations': []})
    assert result.response_type is None
    assert 'response_type' not in result.model_dump()
