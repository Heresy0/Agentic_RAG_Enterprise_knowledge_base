"""Transport checks, no application startup, source traffic or model use."""
import hashlib,json
from pathlib import Path
from types import SimpleNamespace

from scripts.manufacturing_gold_v2_live import make_plan,LIMITS,GOLD,run_case
from scripts.manufacturing_gold_fixture_plan import make_plan as old_plan


def test_plan_binds_v2_not_old_labels_or_consumed_budget():
    plan=make_plan()
    assert plan['dataset']=='manufacturing_gold_100_v2'
    assert plan['gold_cases_sha256']==hashlib.sha256((GOLD/'cases.jsonl').read_bytes()).hexdigest()
    assert plan['gold_cases_sha256']!=old_plan()['gold_cases_sha256']
    assert plan['counts']==dict(manufacturing_qa=28,knowledge_qa=62,conversation_protocol=10)
    assert plan['limits']==LIMITS and plan['request_retries']==0 and not plan['old_results_combined']
    assert LIMITS['index']['max_model_calls']==32


def test_revised_question_is_sent_without_gold_fields(tmp_path):
    case=next(json.loads(s) for s in (GOLD/'cases.jsonl').read_text(encoding='utf-8').splitlines() if json.loads(s)['id']=='MFG-GOLD-061')
    row=dict(case_id=case['id'],question=case['question'],selection={},knowledge_base_id='isolated-kb',route='knowledge_qa')
    path=tmp_path/'bindings.json';path.write_text(json.dumps(dict(cases=[row])),encoding='utf-8')
    calls=[]
    def call(*args,**kwargs):calls.append((args,kwargs));return 'mock'
    assert run_case(SimpleNamespace(call=call),SimpleNamespace(bindings=path,case_id=case['id']))=='mock'
    assert calls==[(('POST','/api/qa'),dict(json=dict(question=case['question'],knowledge_base_id='isolated-kb')))]
    assert '程序名称相同能否证明版本相同' in calls[0][1]['json']['question']


def test_v2_controller_and_gold_paths_are_separate():
    source=Path('scripts/manufacturing_gold_v2_live.py').read_text(encoding='utf-8')
    assert "cfg['upstream_container'] != 'gold100-v2-upstream-mes-20261112-r1'" in source
    assert "ROOT / 'evals/manufacturing_gold_100_v1/cases.jsonl'" not in source
    host=Path('scripts/run_manufacturing_gold100_v2_isolated.py').read_text(encoding='utf-8')
    assert "n.startswith('gold100-v2-')" in host and 'manufacturing_gold_v2_live' in host
    assert "write(run / 'evaluation-attempt.json'" in host
