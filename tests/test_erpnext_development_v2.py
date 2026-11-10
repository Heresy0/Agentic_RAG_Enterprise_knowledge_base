"""The separately frozen v2 batch cannot silently fall back to legacy scoring."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from scripts import accept_erpnext_development as legacy
from scripts import accept_erpnext_development_v2 as runner
from test_erpnext_development import scored_fixture
from backend.app.manufacturing.execution_contracts import context_from_payload
from backend.app.manufacturing.answer_context import ManufacturingAnswerContext, business_citation


def test_v2_keeps_original_questions_gate_and_authorized_limits():
    path=Path('evals/erpnext_zh_v1/suite.json')
    assert runner.load_suite(path)==legacy.load_suite(path)
    assert runner.LIMITS==dict(max_cases=16,max_model_calls=128,max_tokens=800000,max_seconds=1440,reservation_tokens=32000)
    assert runner.SCORER_VERSION=='manufacturing-execution-v2'


@pytest.mark.parametrize('boundary',['calls','tokens','time_reserve','request_time','source'])
def test_v2_budget_stops_before_an_extra_physical_call(monkeypatch,boundary):
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
    count=budget.calls
    with pytest.raises(runner.EvaluationStopped):budget.before_call()
    assert budget.calls==count


def test_v2_array_scoring_is_used_in_required_citations_without_changing_gold():
    case,response,snapshot=scored_fixture()
    case['business_fields'].append('change_notice_refs')
    context=context_from_payload(snapshot['snapshot']['manufacturing']['business_context'])
    item=next(row for row in ManufacturingAnswerContext(context,None).items if row.field_name=='change_notice_refs')
    response['business_citations'].append(business_citation(item,context))
    response=json.loads(json.dumps(response))
    before=deepcopy(case)
    assert not legacy.score_case(case,response,snapshot,{},200)['automatic_proxy_pass']
    result=runner.score_case(case,response,snapshot,{},200)
    assert result['automatic_proxy_pass'] and result['manufacturing_scorer']=='manufacturing-execution-v2'
    assert case==before
    for key,value in [('value',None),('source_record_id','OTHER')]:
        changed=deepcopy(response);changed['business_citations'][-1][key]=value
        assert not runner.score_case(case,changed,snapshot,{},200)['automatic_proxy_pass']


@pytest.mark.parametrize('change',[dict(schema_version='erpnext-prepared-v1'),dict(scorer_version='manufacturing-execution-v1'),dict(runner_sha256='changed')])
def test_execute_rejects_unfrozen_runner_or_scorer_before_attempt_marker(tmp_path,change):
    import hashlib
    frozen=dict(schema_version='erpnext-prepared-v2',scorer_version=runner.SCORER_VERSION,
        runner_sha256=hashlib.sha256(Path(runner.__file__).read_bytes()).hexdigest())
    frozen.update(change)
    (tmp_path/'prepared.json').write_text(json.dumps(frozen),encoding='utf-8')
    with pytest.raises(ValueError,match='入口或评分版本'):
        runner.execute(SimpleNamespace(run_dir=tmp_path),None,None,None)
    assert not (tmp_path/'attempt.json').exists()
