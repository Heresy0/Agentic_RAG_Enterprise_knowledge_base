"""Clarification is a validated user interaction, not a factual claim or refusal."""
import pytest

from test_answer_service import build_service, answer, make_document
from backend.app.knowledge.answer_models import GenerationAnswerDraft
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.context_builder import ContextBuilder


def draft(**changes):
    data=dict(answerable=False,response_type='clarify',claims=[],evidence_gap=None,
        missing_fields=['设备类型'],clarification_questions=['请问需要校准哪一种设备？'],
        coverage=[dict(requirement_id='Q1',aspect='确定校准对象',status='insufficient',claim_indices=[],missing_information='用户未提供设备类型')])
    data.update(changes)
    return GenerationAnswerDraft.model_validate(data)


def test_clarification_survives_validation_and_rendering(monkeypatch):
    model=draft()
    service,*_=build_service(monkeypatch,documents=[make_document('不同设备的校准条件分别规定。')],model_result=model)
    result=answer(service,'校准前需要什么条件？')
    assert result.response_type=='clarify'
    assert result.answer=='请问需要校准哪一种设备？'
    assert result.missing_fields==['设备类型'] and not result.answerable and not result.citations
    assert result.refusal_reason is None


def test_declared_clarification_drops_only_redundant_retrieval_request(monkeypatch):
    model=draft(evidence_gap=dict(reason='missing_evidence',requirement_ids=['Q1'],source_quote='校准对象'))
    service,*_=build_service(monkeypatch,documents=[make_document('不同设备有不同校准要求。')],model_result=model)
    result=answer(service,'这台设备怎样校准？')
    assert result.response_type=='clarify' and result.clarification_questions==model.clarification_questions
    assert model.evidence_gap is not None  # Never mutate the original model output.


def test_declared_clarification_never_hides_factual_claims(monkeypatch):
    model=draft(claims=[dict(text='应当在20度校准',citations=['资料1'])],
                evidence_gap=dict(reason='missing_evidence',requirement_ids=['Q1'],source_quote='校准对象'))
    service,*_=build_service(monkeypatch,documents=[make_document('设备名称尚不明确。')],model_result=model)
    result=answer(service,'这台设备怎样校准？')
    assert result.response_type=='refuse' and not result.citations


@pytest.mark.parametrize('changes',[
    dict(missing_fields=[]),dict(clarification_questions=[]),dict(clarification_questions=['   ']),
    dict(response_type='refuse'),dict(response_type='answer'),
])
def test_inconsistent_or_empty_clarification_is_rejected(changes):
    model=draft(**changes)
    context=ContextBuilder().build([make_document('原文。')])
    assert not AnswerValidator().validate(draft=model,context=context,question='校准前需要什么条件？').valid


def test_concrete_missing_evidence_is_still_refusal(monkeypatch):
    model=GenerationAnswerDraft.model_validate(dict(answerable=False,response_type='refuse',claims=[],refusal_reason='没有指定工单的实时记录。',
        coverage=[dict(requirement_id='Q1',aspect='工单状态',status='insufficient',claim_indices=[],missing_information='缺实时记录')]))
    service,*_=build_service(monkeypatch,documents=[make_document('静态规范不提供实时工单状态。')],model_result=model)
    result=answer(service,'工单MO-123现在完成了吗？')
    assert result.response_type=='refuse' and not result.answerable and not result.clarification_questions
