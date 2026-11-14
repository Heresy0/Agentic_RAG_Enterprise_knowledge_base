"""Offline v4 result release; no model, source API or historical-score changes."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
from scripts.report_manufacturing_gold_live import read,digest,reconcile,public_answer
ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run=args.run_dir;out=args.output
    finished=read(run/'evaluation-finished.json');records=read(run/'evaluation-results.json')
    assert finished['executed']==100 and not finished['stopped'] and len(records)==100
    cases={c['id']:c for c in (json.loads(s) for s in (ROOT/'evals/manufacturing_gold_100_v4/cases.jsonl').read_text(encoding='utf-8').splitlines())}
    reviews=[r for p in sorted(run.glob('assistant-review-*.json')) for r in read(p)]
    assert len(reviews)==100 and {r['case_id'] for r in reviews}==set(cases)
    by_id={r['case_id']:r for r in reviews}
    ledgers=[]
    for plat in ('erp','mes'):
        before={e['id'] for e in read(run/(plat+'-evaluation-ledger-before.json'))}
        ledgers.extend(e for e in read(run/(plat+'-evaluation-ledger-after.json')) if e['id'] not in before)
    usage=reconcile(records,ledgers)
    assert usage['calls']==finished['budget']['calls'] and usage['budget_tokens']==finished['budget']['charged_tokens']
    assert usage['unknown_calls']==finished['budget']['unknown_calls']
    rows=[]
    for r in records:
        review=by_id[r['case_id']];case=cases[r['case_id']]
        assert review['raw_result_sha256']==digest(run/(r['case_id']+'-result.json'))
        assert len(review['required_facts'])==len(case['expected']['required_facts'])
        assert [f['id'] for f in review['required_facts']]==[f['id'] for f in case['expected']['required_facts']]
        assert review['passed']==(all(f['passed'] for f in review['required_facts']) and r['contract']['passed']
            and not review.get('forbidden_violations') and not review.get('unsupported_additions') and not r['harness_error'])
        rows.append(dict(case_id=r['case_id'],category=case['category'],question=case['question'],
            result=public_answer(r['final']),harness_error=r['harness_error'],contract=r['contract'],
            model_calls=sum(e['model_calls'] for e in r['events']),review=review,
            protocol_events=[dict(method=e['method'],path=e['path'],status_code=e['status_code'],model_calls=e['model_calls']) for e in r['events']],
            raw_result_sha256=review['raw_result_sha256']))
    grouped={}
    for category in Counter(c['category'] for c in cases.values()):
        subset=[r for r in rows if r['category']==category]
        grouped[category]=dict(cases=len(subset),contract_passed=sum(r['contract']['passed'] for r in subset),
                               assistant_non_independent_passed=sum(r['review']['passed'] for r in subset))
    summary=dict(schema_version='manufacturing-gold100-live-result-v4',executed=100,
        contract_passed=sum(r['contract']['passed'] for r in rows),
        assistant_non_independent_passed=sum(r['review']['passed'] for r in rows),human_approved=0,independent_holdout=False,
        cases_with_any_model_calls=sum(r['model_calls']>0 for r in rows),zero_model_cases=sum(r['model_calls']==0 for r in rows),
        primary_question_calls_cases=sum(r['final'] is not None and r['final']['model_calls']>0 for r in records),
        categories=grouped,usage=usage,index_usage=dict(calls=0,tokens=0,reused_existing_index=True),
        started_at=finished['budget']['started_at'],finished_at=finished['finished_at'],
        limits=read(run/'bindings.json')['plan']['limits'],budget_origin='explicit_user_authorization',
        request_retries=0,continuations=False,old_results_combined=False,
        old_scores_recomputed=False,
        failed_ids=[r['case_id'] for r in rows if not r['review']['passed']],
        harness_error_ids=[r['case_id'] for r in rows if r['harness_error']],
        preservation=read(run/'preservation-final.json'),runtime_audit=read(run/'runtime-audit.json'),
        auxiliary_observation=read(run/'auxiliary-observations.json'),
        environment_closure=read(run/'temporary-compute-closure.json'))
    out.mkdir(parents=True,exist_ok=True)
    for name,data in [('summary.json',summary),('reviews.json',reviews)]:
        with (out/name).open('x',encoding='utf-8',newline='\n') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    audits={name:read(run/file) for name,file in [('business_source','business-source-proof.json'),
        ('snapshots','snapshot-audit.json'),('v4_preflight','preflight-audit.json'),
        ('inventory_preflight','inventory-preflight-proof.json'),('ledger','evaluation-ledger-proof.json'),
        ('authorization_closure','authorization-closed.json')]}
    with (out/'audits.json').open('x',encoding='utf-8',newline='\n') as f:json.dump(audits,f,ensure_ascii=False,indent=2)
    with (out/'results.jsonl').open('x',encoding='utf-8',newline='\n') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    manifest=dict(input_sha256=read(run/'bindings.json')['input_hashes'],
        bindings_sha256=digest(run/'bindings.json'),
        raw_results={r['case_id']:r['raw_result_sha256'] for r in rows},
        public_files={name:digest(out/name) for name in ('summary.json','reviews.json','results.jsonl','audits.json')},
        labels_sent_to_model=False,credentials_included=False,
        report_implementation_sha256=digest(Path(__file__)))
    with (out/'manifest.json').open('x',encoding='utf-8',newline='\n') as f:json.dump(manifest,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:summary[k] for k in ('executed','contract_passed','assistant_non_independent_passed','usage','categories')},ensure_ascii=False))

if __name__=='__main__':main()
