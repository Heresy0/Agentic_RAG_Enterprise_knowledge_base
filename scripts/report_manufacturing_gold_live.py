"""Offline release of one completed gold100 run; never calls models or runtime APIs."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reconcile(records, ledger_rows):
    events=[e for r in records for request in r['events'] for e in request['trace']['stages'].get('model_usage',[])]
    trace_ids=[e['event_id'] for e in events]
    ledger_ids=[e['id'] for e in ledger_rows]
    if len(set(trace_ids))!=len(trace_ids) or len(set(ledger_ids))!=len(ledger_ids) or set(trace_ids)!=set(ledger_ids):
        raise ValueError('Trace/independent ledger IDs differ or contain duplicates.')
    if any(e['status']=='pending' for e in ledger_rows):
        raise ValueError('Pending physical calls must be resolved before reporting.')
    return dict(matched=True,calls=len(ledger_rows),known_tokens=sum(e['total_tokens'] or 0 for e in ledger_rows),
        unknown_calls=sum(e['total_tokens'] is None for e in ledger_rows),
        budget_tokens=sum(e['total_tokens'] if e['total_tokens'] is not None else 32000 for e in ledger_rows),
        by_stage=dict(Counter(e['stage'] for e in ledger_rows)),
        providers_models=sorted({e['provider']+':'+e['model'] for e in ledger_rows}),
        all_priced=all(e['estimated_cost'] is not None for e in ledger_rows),pending=0)


def public_answer(raw):
    if raw is None:
        return None
    body=raw['response']
    keys=('answer','answerable','refusal_reason','response_type','missing_fields','clarification_questions',
          'citations','business_citations','claim_bindings','work_order_context','binding')
    result={k:body[k] for k in keys if k in body}
    if 'detail' in body:
        result['detail']=body['detail']
    if 'applicability' in body:
        result['applicability']={k:v for k,v in body['applicability'].items() if k in
            ('state','reason','document_code','business_revision','rule_ids','gaps')}
        if body['applicability'].get('step_guidance'):
            result['applicability']['step_guidance']={k:v for k,v in body['applicability']['step_guidance'].items()
                if k in ('revision','document_code','business_revision','operation_code','basis','source_revision')}
    if 'turns' in body:
        result['turns']=[dict(turn_index=r['turn_index'],question=r['question'],result=public_answer(
            dict(response=r['result']))) for r in body['turns']]
    return dict(status_code=raw.get('status_code',200),body=result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run=args.run_dir;out=args.output
    finished=read(run/'evaluation-finished.json');records=read(run/'evaluation-results.json')
    assert finished['executed']==100 and not finished['stopped'] and len(records)==100
    cases={c['id']:c for c in (json.loads(s) for s in (ROOT/'evals/manufacturing_gold_100_v1/cases.jsonl').read_text(encoding='utf-8').splitlines())}
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
        assert review['passed']==(all(f['passed'] for f in review['required_facts']) and r['contract']['passed'])
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
    summary=dict(schema_version='manufacturing-gold100-live-result-v1',executed=100,
        contract_passed=sum(r['contract']['passed'] for r in rows),
        assistant_non_independent_passed=sum(r['review']['passed'] for r in rows),human_approved=0,independent_holdout=False,
        cases_with_any_model_calls=sum(r['model_calls']>0 for r in rows),zero_model_cases=sum(r['model_calls']==0 for r in rows),
        primary_question_calls_cases=sum(r['final'] is not None and r['final']['model_calls']>0 for r in records),
        categories=grouped,usage=usage,index_usage=read(run/'index-ledger-proof.json'),
        started_at=finished['budget']['started_at'],finished_at=finished['finished_at'],
        limits=read(run/'bindings.json')['plan']['limits'],budget_origin='operator_safety_ceilings',
        request_retries=0,continuations=False,old_results_combined=False,
        failed_ids=[r['case_id'] for r in rows if not r['review']['passed']],
        harness_error_ids=[r['case_id'] for r in rows if r['harness_error']],
        preservation=read(run/'preservation-final.json'),runtime_audit=read(run/'runtime-audit.json'),
        auxiliary_observation='Q040附加登记措辞进入2次模型后校验拒答，未执行写入；单独保留，不另计黄金题。')
    out.mkdir(parents=True,exist_ok=True)
    for name,data in [('summary.json',summary),('reviews.json',reviews)]:
        with (out/name).open('x',encoding='utf-8',newline='\n') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    audits={name:read(run/file) for name,file in [('business_source','business-source-proof.json'),
        ('snapshots','snapshot-audit.json'),('numeric_identifier_probe','numeric-identifier-offline-probe.json')]}
    with (out/'audits.json').open('x',encoding='utf-8',newline='\n') as f:json.dump(audits,f,ensure_ascii=False,indent=2)
    with (out/'results.jsonl').open('x',encoding='utf-8',newline='\n') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    manifest=dict(input_sha256=read(run/'bindings.json')['input_hashes'],
        bindings_sha256=digest(run/'bindings.json'),
        raw_results={r['case_id']:r['raw_result_sha256'] for r in rows},
        public_files={name:digest(out/name) for name in ('summary.json','reviews.json','results.jsonl','audits.json')},
        labels_sent_to_model=False,credentials_included=False)
    with (out/'manifest.json').open('x',encoding='utf-8',newline='\n') as f:json.dump(manifest,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:summary[k] for k in ('executed','contract_passed','assistant_non_independent_passed','usage','categories')},ensure_ascii=False))


if __name__=='__main__':
    main()
