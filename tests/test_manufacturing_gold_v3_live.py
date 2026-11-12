"""Frozen v3 routing/budget and primary-versus-observation failure accounting."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.manufacturing_gold_v3_live import GOLD,LIMITS,Runtime,LegacyRuntime,make_plan,run_case


def test_full_v3_plan_has_new_dataset_and_authorized_limits_without_index_calls():
    plan=make_plan()
    assert plan['dataset']=='manufacturing_gold_100_v3' and len(plan['cases'])==100
    assert plan['gold_cases_sha256']==hashlib.sha256((GOLD/'cases.jsonl').read_bytes()).hexdigest()
    assert plan['limits']['evaluation']==dict(max_cases=100,max_model_calls=800,max_tokens=5000000,max_seconds=9000,reservation_tokens=32000)
    assert LIMITS['index']['max_model_calls']==LIMITS['index']['max_tokens']==0
    assert not plan['old_results_combined'] and plan['request_retries']==0


def test_payload_contains_original_question_and_selection_without_gold_or_policy(tmp_path):
    c=next(json.loads(s) for s in (GOLD/'cases.jsonl').read_text(encoding='utf-8').splitlines() if json.loads(s)['id']=='MFG-GOLD-006')
    path=tmp_path/'bindings.json'
    path.write_text(json.dumps(dict(cases=[dict(case_id=c['id'],route='knowledge_qa',selection={},knowledge_base_id=None,question=c['question'])])),encoding='utf-8')
    calls=[]
    def call(*args,**kwargs):calls.append((args,kwargs));return 'result'
    assert run_case(SimpleNamespace(call=call),SimpleNamespace(bindings=path,case_id=c['id']))=='result'
    assert calls==[(('POST','/api/qa'),dict(json=dict(question=c['question'],knowledge_base_id=None)))]


def test_snapshot_failure_retains_primary_response_and_still_raises(monkeypatch,tmp_path):
    primary=dict(method='POST',path='/api/qa',status_code=200,response=dict(run_id='run',answer='已交付'))
    def fail_after_primary(self,*args,**kwargs):
        self.events.append(primary)
        raise ValueError('snapshot authentication failure')
    monkeypatch.setattr(LegacyRuntime,'call',fail_after_primary)
    rt=Runtime(None,SimpleNamespace(run_dir=tmp_path),None)
    with pytest.raises(ValueError,match='snapshot'):
        rt.call('POST','/api/qa',json=dict(question='原问题'))
    assert rt.observation_failure==dict(question='原问题',response=primary)
    assert 'snapshot_read_status' not in primary


def test_v3_fault_controller_cannot_modify_the_existing_v2_clone():
    source=Path('scripts/manufacturing_gold_v3_live.py').read_text(encoding='utf-8')
    assert "cfg['upstream_container'] != 'gold100-v3-upstream-mes-20261112-r1'" in source
    assert 'gold100-v2-upstream-mes' not in source
