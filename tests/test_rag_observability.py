import json
from pathlib import Path

import pytest
from prometheus_client import REGISTRY, generate_latest

from test_answer_service import build_service, make_document, answer
from test_evidence_feedback import setup, complete, execute
from backend.app.evaluation.trace import record_rag_event, record_answer_decision
from backend.app.knowledge.answer_models import AnswerDraft, AnswerClaim
from backend.app.knowledge.rag_budget import RagBudget, RagBudgetExceeded


def value(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0


@pytest.mark.parametrize('budget,action,reason', [
    (RagBudget(max_calls=0),'model','calls'),
    (RagBudget(max_tokens=1),'model','tokens'),
    (RagBudget(max_seconds=0),'model','time'),
    (RagBudget(max_searches=1),'search','searches'),
    (RagBudget(max_reads=0),'read','reads'),
])
def test_actual_shared_quota_denial_is_recorded_without_dispatch(budget,action,reason):
    with pytest.raises(RagBudgetExceeded):
        if action=='model':
            with budget.model_attempt(prompt_bytes=10,output_tokens=10):
                pytest.fail('must not dispatch')
        else:
            with budget.tool_attempt(action):
                pytest.fail('must not dispatch')
    assert budget.snapshot()['limited_by']==reason
    before=value('enterprise_rag_budget_requests_total',outcome='limited',reason=reason)
    record_rag_event(dict(kind='budget',**budget.snapshot()))
    assert value('enterprise_rag_budget_requests_total',outcome='limited',reason=reason)==before+1
    assert budget.calls==budget.reads==0 and budget.searches==1


def test_reaching_quota_and_duplicate_query_do_not_claim_budget_exhaustion():
    budget=RagBudget(max_calls=1)
    with budget.model_attempt(prompt_bytes=10,output_tokens=10):
        pass
    assert budget.limited_by is None
    budget.note_query('private-query')
    with pytest.raises(RagBudgetExceeded),budget.tool_attempt('search',query='private-query'):
        pass
    assert budget.limited_by is None
    before=value('enterprise_rag_budget_requests_total',outcome='used',reason='none')
    record_rag_event(dict(kind='budget',**budget.snapshot()))
    assert value('enterprise_rag_budget_requests_total',outcome='used',reason='none')==before+1


def test_normal_pipeline_counts_one_terminal_result_and_one_budget_without_trace(monkeypatch):
    service,*_=build_service(monkeypatch,documents=[make_document('温度是80摄氏度。')],
        model_result=AnswerDraft(answerable=True,claims=[AnswerClaim(text='温度是80摄氏度',citations=['资料1'])]))
    result_before=value('enterprise_rag_terminal_results_total',stage='final',answerable='true')
    budget_before=value('enterprise_rag_budget_requests_total',outcome='unused',reason='none')
    result=answer(service,'温度是多少？')
    assert result.answerable
    assert value('enterprise_rag_terminal_results_total',stage='final',answerable='true')==result_before+1
    assert value('enterprise_rag_budget_requests_total',outcome='unused',reason='none')==budget_before+1


@pytest.mark.parametrize('candidate,answerable',[(complete(),'true'),(complete(temperature=999),'false')])
def test_feedback_metric_uses_final_validation_not_draft_answerability(monkeypatch,candidate,answerable):
    service,*_=setup(monkeypatch,candidate=candidate)
    before=value('enterprise_rag_feedback_final_total',answerable=answerable)
    result,trace=execute(service)
    assert str(result.answerable).lower()==answerable
    assert value('enterprise_rag_feedback_final_total',answerable=answerable)==before+1
    assert len([e for e in trace.stages['rag_routing'] if e.get('kind')=='feedback_final'])==1


def test_nonterminal_generation_and_private_fields_never_become_metric_labels():
    before=value('enterprise_rag_terminal_results_total',stage='generation',answerable='true')
    record_answer_decision(stage='generation',answerable=True,reason='private-customer-question')
    record_rag_event(dict(kind='schema_repair',outcome='generated',query='private-customer-question'))
    assert value('enterprise_rag_terminal_results_total',stage='generation',answerable='true')==before
    body=generate_latest().decode()
    assert 'private-customer-question' not in body and 'private-query' not in body


def test_existing_dashboard_identity_is_preserved_and_new_panels_do_not_overlap():
    d=json.loads(Path('monitoring/grafana/dashboards/enterprise-rag.json').read_text(encoding='utf-8'))
    assert d['uid']=='enterprise-knowledge-rag'
    panels=d['panels'];assert len({p['id'] for p in panels})==len(panels)
    assert all(p['datasource']['uid']=='prometheus' for p in panels if p['type']!='row')
    for i,a in enumerate(panels):
        for b in panels[i+1:]:
            x,y=a['gridPos'],b['gridPos']
            assert (x['x']+x['w']<=y['x'] or y['x']+y['w']<=x['x']
                    or x['y']+x['h']<=y['y'] or y['y']+y['h']<=x['y'])
