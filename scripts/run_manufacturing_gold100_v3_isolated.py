"""Host coordinator: one frozen v3 100-case run in pre-provisioned gold100 clones.

Requires an explicit user authorization and completed private preparation receipts.
No retries, no result replacement, no old/new score aggregation. Original services
are never started, stopped, rebuilt or written by this coordinator.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

from scripts.manufacturing_gold_v3_live import make_plan

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def docker(args):
    p = subprocess.run(['docker', *args], capture_output=True)
    if p.returncode:
        raise RuntimeError('Docker operation failed; inspect private execution logs.')
    return p.stdout


def ledger(container):
    code = """import json
from sqlalchemy import create_engine,text
from backend.app.config import Settings
with create_engine(Settings().database_url).connect() as c:
 print(json.dumps([dict(r) for r in c.execute(text('select * from model_usage_events order by created_at,id')).mappings()],default=str))"""
    return json.loads(docker(['exec', '-w', '/app', container, 'python', '-c', code]))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--prepare-only', action='store_true')
    a = p.parse_args(); run = a.run_dir.resolve()
    run.relative_to(ROOT / 'tmp')
    setup = read(run / 'setup-receipt.json')
    targets = {key: row['container'] for key, row in setup['targets'].items()}
    assert set(targets) == {'erp', 'mes'} and all(n.startswith('gold100-v3-') for n in targets.values())
    adapter = setup['adapter']; assert adapter.startswith('gold100-v3-')
    if a.prepare_only:
        rows = []; inputs = {}; baselines = {}
        for plat, name in targets.items():
            for filename in (plat + '-bindings.json', 'zero-model-probe.json'):
                docker(['cp', name + ':/tmp/gold100/' + filename, str(run / (plat + '-' + filename))])
            bindings = read(run / (plat + '-' + plat + '-bindings.json'))
            probe = read(run / (plat + '-zero-model-probe.json'))
            assert probe['budget']['calls'] == 0 and len(bindings['inventory']) == 55
            rows.extend(bindings['cases'])
            inputs[plat] = {k:v for k,v in bindings.items() if k != 'cases'}
            baselines[plat] = ledger(name)
            write(run / (plat + '-evaluation-ledger-before.json'), baselines[plat])
        rows.sort(key=lambda x: x['case_id'])
        assert [r['case_id'] for r in rows] == [f'MFG-GOLD-{i:03d}' for i in range(1,101)]
        plan = make_plan()
        hashes = {str(path.relative_to(ROOT)).replace('\\','/'): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in [ROOT/'scripts/manufacturing_gold_v3_live.py',ROOT/'scripts/manufacturing_gold_live.py',ROOT/'scripts/manufacturing_gold_fixture_plan.py',
                               ROOT/'scripts/prepare_manufacturing_gold_runtime.py', ROOT/'scripts/run_manufacturing_gold100_v3_isolated.py',ROOT/'scripts/accept_erpnext_development_v2.py',
                               ROOT/'evals/manufacturing_gold_100_v3/cases.jsonl',ROOT/'evals/manufacturing_gold_100_v3/manifest.json',
                               ROOT/'evals/manufacturing_gold_100_v3/review_policy.json']}
        frozen = dict(schema_version='gold100-live-bindings-v3', cases=rows, inventories=inputs, input_hashes=hashes,
                      plan=plan, source_revision=setup['source_revision'], provenance='assistant_non_independent_regression',
                      user_authorization='当前版本与v3黄金集隔离完整100题，允许相同范围数据及既有百炼端点；800调用/500万Token/9000秒，不重试不补跑',
                      ordinary_request_payload_only=True, labels_sent_to_model=False, old_results_combined=False)
        write(run / 'bindings.json', frozen)
        print(json.dumps(dict(prepared_cases=100, platforms={k:sum(r['platform']==k for r in rows) for k in targets},model_calls=0)))
        return
    frozen = read(run / 'bindings.json')
    for path, digest in frozen['input_hashes'].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == digest, 'Frozen program/input changed'
    # This marker is exclusive: even a failed invocation cannot silently restart a batch.
    started = datetime.now(timezone.utc).isoformat()
    write(run / 'evaluation-attempt.json', dict(started_at=started, cases=100, retries=0,
        limits=frozen['plan']['limits']['evaluation'], bindings_sha256=hashlib.sha256((run/'bindings.json').read_bytes()).hexdigest()))
    state = dict(started_at=started,calls=0,charged_tokens=0,unknown_calls=0,limited_by=None)
    (run/'global-budget.json').write_text(json.dumps(state),encoding='utf-8')
    for name in targets.values():
        docker(['cp',str(run/'bindings.json'), name+':/tmp/gold100/bindings.json'])
    results = []; stopped = None
    for row in frozen['cases']:
        if state['limited_by']:
            stopped=state['limited_by']; break
        name = targets[row['platform']]; case_id = row['case_id']
        docker(['cp',str(run/'global-budget.json'),name+':/tmp/gold100/global-budget.json'])
        stop_adapter = case_id == 'MFG-GOLD-034'
        try:
            if stop_adapter:
                docker(['stop','--time','10',adapter])
            command=['exec','-e','LOG_LEVEL=ERROR','-w','/app',name,'python','-u','-m','scripts.manufacturing_gold_v3_live','case',
                     '--run-dir','/tmp/gold100','--tokens','/tmp/gold-tokens.json','--aliases','/tmp/gold100/aliases.json',
                     '--control','/tmp/gold100/control.json','--bindings','/tmp/gold100/bindings.json',
                     '--budget','/tmp/gold100/global-budget.json','--case-id',case_id]
            proc=subprocess.run(['docker',*command],capture_output=True)
            (run/(case_id+'-stdout.log')).write_bytes(proc.stdout)
            (run/(case_id+'-stderr.log')).write_bytes(proc.stderr)
            if proc.returncode:
                stopped='runner_process_failure'; break
            docker(['cp',name+':/tmp/gold100/'+case_id+'-result.json',str(run/(case_id+'-result.json'))])
            result=read(run/(case_id+'-result.json'));results.append(result)
            state.update({k:v for k,v in result['budget'].items() if k in ('calls','charged_tokens','unknown_calls','limited_by')})
            (run/'global-budget.json').write_text(json.dumps(state),encoding='utf-8')
            print(json.dumps(dict(case_id=case_id,http=result['final']['status_code'] if result['final'] else None,
                contract=result['contract']['passed'],calls=state['calls'],budget_tokens=state['charged_tokens'],harness_error=result['harness_error'])),flush=True)
        finally:
            if stop_adapter:
                docker(['start',adapter]); time.sleep(2)
    for plat,name in targets.items():
        write(run/(plat+'-evaluation-ledger-after.json'),ledger(name))
    write(run/'evaluation-results.json',results)
    write(run/'evaluation-finished.json',dict(executed=len(results),expected=100,stopped=stopped,
        budget=state,finished_at=datetime.now(timezone.utc).isoformat(),old_results_combined=False,
        semantic_review='pending_assistant_non_independent',request_retries=0))
    print(json.dumps(dict(executed=len(results),stopped=stopped,budget=state)),flush=True)


if __name__=='__main__':
    main()
