import pytest

from test_answer_service import make_document, build_service, answer
from test_answer_repair import repair_service, draft
from test_evidence_feedback import setup, complete, missing, QUESTION, budget
from backend.app.evaluation.trace import capture_trace
from backend.app.knowledge.answer_models import AnswerClaim, AnswerDraft, GenerationAnswerDraft
from backend.app.knowledge.answer_completion import complete_explicit_cause
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.answer_coverage import question_requirements
from backend.app.knowledge.calculation_repair import normalize_calculations, verified_calculation_repair
from backend.app.knowledge.calculation_service import compute
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.security.authorization import AuthorizationDenied


def context(text):
    return ContextBuilder().build([make_document(text)])


def calc_draft(*, quote='2分钟', value='120', unit='秒'):
    return GenerationAnswerDraft.model_validate(dict(answerable=True, claims=[dict(
        text='待程序计算', citations=['资料1'], calculation=dict(operation='time_add', inputs=[
            dict(source='问题', quote='08:00:00', value='08:00:00', unit='时刻'),
            dict(source='资料1', quote=quote, value=value, unit=unit)], result=None))],
        coverage=[dict(requirement_id='Q1', aspect='最终时刻', status='answered', claim_indices=[1], missing_information=None)]))


def test_equivalent_units_are_normalized_and_strictly_verified():
    original = calc_draft()
    fixed = normalize_calculations(original, context('任务持续2分钟。'), '08:00:00开始，何时结束？')
    assert fixed.claims[0].calculation.inputs[1].unit == '分钟'
    assert original.claims[0].calculation.inputs[1].unit == '秒'
    assert compute(fixed.claims[0], context('任务持续2分钟。'), '08:00:00开始，何时结束？').result == '08:02:00'


@pytest.mark.parametrize('quote,value', [('2分钟','121'),('20分钟','120'),('2分钟','-120'),('3分钟','180')])
def test_wrong_values_or_fabricated_quotes_are_not_normalized_into_validity(quote, value):
    original = calc_draft(quote=quote, value=value)
    fixed = normalize_calculations(original, context('任务持续2分钟。'), '08:00:00开始，何时结束？')
    assert fixed == original


def test_spurious_calculation_on_verbatim_fact_is_removed_without_model_call():
    original = calc_draft()
    original.claims[0].text = '任务持续2分钟。'
    fixed = normalize_calculations(original, context('任务持续2分钟。'), '任务多久？')
    assert fixed.claims[0].calculation is None
    assert AnswerValidator().validate(draft=fixed, context=context('任务持续2分钟。'), question='任务多久？').valid


def test_calculation_model_repair_must_use_only_program_results_or_whole_source_sentences():
    original = calc_draft()
    candidate = calc_draft(quote='2分钟', value='2', unit='分钟')
    assert verified_calculation_repair(original, candidate, context('任务持续2分钟。'), '08:00:00开始，何时结束？')
    candidate.claims.append(AnswerClaim(text='无需授权审核。', citations=['资料1']))
    assert not verified_calculation_repair(original, candidate, context('任务持续2分钟。'), '08:00:00开始，何时结束？')
    candidate.claims = [AnswerClaim(text='需要审核', citations=['资料1'])]
    assert not verified_calculation_repair(original, candidate, context('该任务不需要审核。'), '任务多久？')


def schema_service(monkeypatch, *, candidate=None, hook=None):
    service, retrieval, *_ = build_service(monkeypatch, documents=[make_document('温度是80摄氏度。')], model_result=None)
    service._settings.rag_repair_enabled = True
    retrieval.refresh_evidence = lambda docs, **kwargs: docs
    def invalid(messages):
        return GenerationAnswerDraft.model_validate({'answerable':True, 'coverage':[]})
    service._structured_llm.invoke = invalid
    service._answer_repair, calls, guard = repair_service(candidate or draft(indices=(1,)), hook=hook)
    return service, calls, guard


def test_schema_failure_gets_one_shared_budget_repair_and_safe_field_diagnostics(monkeypatch):
    service, calls, _ = schema_service(monkeypatch)
    with capture_trace() as trace:
        result = answer(service, '温度是多少？')
    assert result.answerable and '80' in result.answer and len(calls) == 1
    event = next(e for e in trace.stages['rag_routing'] if e['kind']=='schema_error')
    assert event['errors'] == [{'location':['coverage'], 'type':'too_short'}]
    assert 'input' not in str(event) and budget(trace)['calls'] == 1
    assert trace.stages['answer_generation'][0]['response']['answerable'] is False
    assert trace.stages['answer_schema_recovery'][0]['response']['answerable'] is True


def test_schema_second_failure_returns_explicit_failure_without_unbounded_retry(monkeypatch):
    service, calls, _ = schema_service(monkeypatch, candidate=RuntimeError('private error details'))
    with capture_trace() as trace:
        result = answer(service, '温度是多少？')
    assert not result.answerable and not result.citations and len(calls) == 1
    assert '格式异常' in result.refusal_reason
    assert 'private error details' not in str(trace.to_dict())


def test_schema_recovery_revocation_is_terminal(monkeypatch):
    service, calls, _ = schema_service(monkeypatch, hook=lambda guard: setattr(guard,'allowed',False))
    with pytest.raises(AuthorizationDenied):
        answer(service, '温度是多少？')
    assert len(calls) == 1


def test_schema_recovery_stops_if_evidence_changed(monkeypatch):
    service, calls, _ = schema_service(monkeypatch)
    service._retrieval_service.refresh_evidence = lambda *args, **kwargs: []
    result = answer(service, '温度是多少？')
    assert not result.answerable and not calls


def test_explicit_cause_is_completed_for_generic_object_without_model_call():
    ctx = context('TASK-Z91检查发现2条对象缺失，原因是映射清单漏掉新目录。')
    original = AnswerDraft(answerable=True, claims=[AnswerClaim(text='TASK-Z91发现2条对象缺失。', citations=['资料1'])])
    decision = complete_explicit_cause('TASK-Z91为什么失败？', original, ctx)
    assert decision['status']=='appended' and '映射清单漏掉新目录' in original.claims[-1].text
    assert complete_explicit_cause('TASK-Z91为什么失败？', original, ctx)['status']=='already_present'
    assert AnswerValidator().validate(draft=original, context=ctx, question='TASK-Z91为什么失败？').valid


@pytest.mark.parametrize('question,text', [
    ('TASK-Z91为什么失败？','TASK-Z92失败，原因是清单漏项。'),
    ('TASK-Z91为什么失败？','TASK-Z91失败，可能原因是清单漏项。'),
    ('TASK-Z91为什么失败？','TASK-Z91已经通过。TASK-Z92失败，原因是清单漏项。'),
    ('TASK-Z91什么时候结束？','TASK-Z91失败，原因是清单漏项。'),
])
def test_cause_never_crosses_object_or_interprets_speculation(question,text):
    original = AnswerDraft(answerable=True, claims=[AnswerClaim(text='检查失败。', citations=['资料1'])])
    assert complete_explicit_cause(question,original,context(text)) is None
    assert len(original.claims)==1


def test_conflicting_explicit_causes_stay_untouched():
    ctx=ContextBuilder().build([make_document('TASK-Z91失败，原因是权限不足。',chunk_id='a'),
                               make_document('TASK-Z91失败，原因是清单漏项。',chunk_id='b')])
    original=AnswerDraft(answerable=True, claims=[AnswerClaim(text='检查失败。',citations=['资料1'])])
    assert complete_explicit_cause('TASK-Z91为什么失败？',original,ctx)['status']=='skipped_ambiguous_causes'
    assert len(original.claims)==1


def test_whole_question_gap_can_narrow_to_one_literal_missing_aspect(monkeypatch):
    initial=missing(quote=QUESTION)
    initial.coverage[0].aspect='乙工艺'
    service,retrieval,generation,calls,*_=setup(monkeypatch,candidate=complete(),first=initial)
    with capture_trace() as trace:
        result=answer(service,QUESTION)
    assert result.answerable and len(retrieval.calls)==2 and len(calls)==1
    assert '重点查证：乙工艺' in retrieval.calls[1]['query']
    assert budget(trace)['feedback_attempts']==1 and budget(trace)['regenerations']==1


@pytest.mark.parametrize('premise',['均为北京时间。','币种为人民币。','单位为分钟。','假设每次立即失败。'])
def test_declarative_premise_is_preserved_with_original_question(premise):
    rows=question_requirements('最终结果是多少？'+premise)
    assert len(rows)==1 and premise in rows[0].text


def test_actual_followup_question_is_never_merged_or_dropped():
    rows=question_requirements('最终结果是多少？时区为哪里？')
    assert len(rows)==2 and rows[1].text=='时区为哪里？'
