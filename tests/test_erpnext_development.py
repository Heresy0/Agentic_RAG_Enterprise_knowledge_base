"""The paid-batch harness is tested without network/provider calls."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

import pytest

from scripts import accept_erpnext_development as runner
from backend.app.manufacturing.execution_contracts import observed_context
from backend.app.manufacturing.answer_context import ManufacturingAnswerContext, business_citation
from backend.app.manufacturing.service import public_context
from backend.app.integrations.erpnext import project_work_order, selected_operation, job_card_choice, execution_record
from test_erpnext_adapter import config
from test_erpnext_execution import ERPGraph


def test_suite_is_separate_exactly_16_and_preserves_boundaries():
    suite = runner.load_suite(Path('evals/erpnext_zh_v1/suite.json'))
    assert len(suite['cases']) == 16
    assert sum(row['expected_model_path'] for row in suite['cases']) == 12
    assert [row['id'] for row in suite['cases']] == [f'ERPDEV-Q{i:03}' for i in range(1,17)]
    assert suite['cases'][-2]['expected_http_status'] == 404
    assert suite['cases'][-1]['expected_behavior'] == 'refuse'


@pytest.mark.parametrize('changes',[dict(max_cases=17),dict(max_model_calls=129),dict(max_tokens=800001),dict(max_seconds=1441)])
def test_suite_cannot_expand_authorization(tmp_path,changes):
    suite=runner.load_suite(Path('evals/erpnext_zh_v1/suite.json'));suite['limits'].update(changes)
    path=tmp_path/'suite.json';path.write_text(json.dumps(suite),encoding='utf-8')
    with pytest.raises(ValueError):runner.load_suite(path)


@pytest.mark.parametrize('boundary',['calls','tokens','time_reserve','request_time','source'])
def test_physical_call_budget_stops_before_increment(monkeypatch,boundary):
    monkeypatch.setattr(runner,'monotonic',lambda:100)
    monkeypatch.setattr('backend.app.quality.eval_budget.monotonic',lambda:100)
    budget=runner.BatchBudget(dict(runner.LIMITS));budget.started=0
    if boundary=='calls':budget.calls=128
    elif boundary=='tokens':budget.charged_tokens=768001
    elif boundary=='time_reserve':budget.started=-1300
    elif boundary=='request_time':budget.request_deadline=99
    else:
        def denied():raise PermissionError()
        budget.guard=denied
    before=budget.calls
    with pytest.raises(runner.EvaluationStopped):budget.before_call()
    assert budget.calls==before and budget.limited_by==('source_or_authorization' if boundary=='source' else boundary)


def test_unknown_usage_is_reserved_without_retry_or_free_call(monkeypatch):
    monkeypatch.setattr(runner,'monotonic',lambda:100)
    monkeypatch.setattr('backend.app.quality.eval_budget.monotonic',lambda:100)
    budget=runner.BatchBudget(dict(runner.LIMITS));budget.started=0
    budget.before_call();budget.after_call(None)
    assert budget.calls==1 and budget.unknown_calls==1 and budget.charged_tokens==32000


def scored_fixture():
    graph=ERPGraph();cfg=config();op=selected_operation(graph.order,'ROW-A')
    record=execution_record(project_work_order(graph.order,cfg,{'HC-P01'}),op,job_card_choice(graph.cards['JC-A'],graph.order,op,cfg))
    frozen=observed_context(record,fetched_at=datetime(2026,11,9,tzinfo=timezone.utc),last_verified_at=datetime(2026,11,9,tzinfo=timezone.utc))
    current=frozen.model_copy(update=dict(fetched_at=frozen.fetched_at+timedelta(minutes=1),last_verified_at=frozen.last_verified_at+timedelta(minutes=1)))
    fields=['actual_started_at','operation_actual_started_at','job_card_actual_started_at']
    case=dict(id='test',expected_model_path=True,answerable=True,expected_behavior='answer',required_facts=[],business_fields=fields,
        business_context_binding=dict(context=frozen.model_dump(mode='json')),applicability_binding=dict(applicability={}))
    citations=[business_citation(item,current) for item in ManufacturingAnswerContext(current,None).items if item.field_name in fields]
    response=dict(answerable=True,response_type='answer',answer='分别核对三层开工来源。',citations=[],business_citations=citations,
        work_order_context=public_context(current),applicability={})
    snapshot=dict(snapshot=dict(context=dict(items=[]),manufacturing=dict(business_context=current.model_dump(mode='json'),
        validation=dict(source_verified=True,document_scope_verified=True,business_citations_verified=True,mes_reads=3),applicability={})))
    return case,response,snapshot


def test_only_volatile_timestamps_are_aligned_and_gold_is_not_mutated():
    case,response,snapshot=scored_fixture();before=deepcopy(case)
    assert runner.score_case(case,response,snapshot,{},200)['automatic_proxy_pass']
    assert case==before


@pytest.mark.parametrize('bad',['parent_time','card_identity','snapshot_record','missing_citation'])
def test_scoring_cannot_adapt_business_truth_after_response(bad):
    case,response,snapshot=scored_fixture()
    if bad=='parent_time':response['work_order_context']['actual_started_at']='2026-10-26T09:30:00+08:00'
    elif bad=='card_identity':response['business_citations'][0]['job_card_id']='FOREIGN'
    elif bad=='snapshot_record':snapshot['snapshot']['manufacturing']['business_context']['record']['status']='changed'
    else:response['business_citations']=[]
    assert not runner.score_case(case,response,snapshot,{},200)['automatic_proxy_pass']


def test_permission_case_cannot_pass_with_disclosure_or_model_usage():
    case=dict(expected_http_status=404)
    assert runner.score_case(case,dict(detail='不可访问'),{}, {},404)['automatic_proxy_pass']
    assert not runner.score_case(case,dict(detail='HC-A17'),{}, {},404)['automatic_proxy_pass']
    assert not runner.score_case(case,dict(detail='不可访问'),{},dict(stages=dict(model_usage=[{}])),404)['automatic_proxy_pass']
