from datetime import datetime, timezone
import pytest

from scripts.manufacturing_gold_fixture_plan import make_plan, LIMITS
from scripts.manufacturing_gold_live import resolve_alias, selection, contracts, RunBudget
from backend.app.quality.eval_budget import EvaluationStopped


def test_plan_keeps_document_and_live_routes_distinct():
    plan = make_plan()
    assert plan['counts'] == dict(manufacturing_qa=28, knowledge_qa=62, conversation_protocol=10)
    protocols = [r for r in plan['cases'] if r['requires_alias_resolution']]
    assert len(protocols) == 6
    assert all(r['platform'] == 'mes' and r['knowledge_scope'] == 'expanded_chinese' for r in protocols)
    assert plan['request_retries'] == 0 and not plan['old_results_combined']


def test_aliases_resolve_real_selection_and_never_supply_extra_source_field():
    aliases = {'DEMO-WO-B2101': dict(source_system_id='openmes-hc',work_order_id='4',batch_id='7',step_id='13')}
    raw = dict(source_system_id='ISOLATED-MES-B21',work_order_id='DEMO-WO-B2101',batch_id='DEMO-BATCH',step_id='DEMO-STEP')
    assert resolve_alias(raw, aliases) == dict(work_order_id='4',batch_id='7',step_id='13')
    with pytest.raises(KeyError):
        resolve_alias(dict(raw,work_order_id='UNKNOWN'), aliases)
    assert selection(dict(work_order_id='1',source_system_id='openmes-hc',token='secret')) == dict(work_order_id='1')


def test_contract_does_not_turn_http_success_into_semantic_accuracy():
    case = dict(id='MFG-GOLD-041', expected=dict(http_status=None,answerable=True,behavior='answer'))
    row = dict(status_code=200,response=dict(answer='错误但结构合法',answerable=True,citations=[dict(id='1')]))
    score = contracts(case,row)
    assert score['passed'] and score['semantic_score'] is False
    row['response']['citations'] = []
    assert not contracts(case,row)['passed']


def test_error_status_cannot_leak_business_context():
    case = dict(id='MFG-GOLD-029',expected=dict(http_status=404,answerable=False,behavior='error'))
    row = dict(status_code=404,response=dict(detail='不可访问',work_order_context=dict(work_order_id='1')))
    assert not contracts(case,row)['passed']


@pytest.mark.parametrize('calls,tokens,reason',[(800,0,'calls'),(0,5000000-31999,'tokens')])
def test_persisted_global_budget_stops_before_another_physical_call(calls,tokens,reason):
    state=dict(started_at=datetime.now(timezone.utc).isoformat(),calls=calls,charged_tokens=tokens,unknown_calls=0)
    budget=RunBudget(state)
    with pytest.raises(EvaluationStopped,match=reason):budget.before_call()
    assert budget.calls == calls


def test_unknown_usage_reservation_survives_process_boundary():
    state=dict(started_at=datetime.now(timezone.utc).isoformat(),calls=103,charged_tokens=23000,unknown_calls=1)
    budget=RunBudget(state);budget.before_call();budget.after_call(None)
    assert budget.snapshot()['calls'] == 104
    assert budget.charged_tokens == 55000 and budget.unknown_calls == 2
