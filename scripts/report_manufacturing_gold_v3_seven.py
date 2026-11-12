"""Release one completed seven-case batch offline; never rescore the old hundred."""
import argparse
import json
from pathlib import Path

from scripts.build_manufacturing_gold_100_v3 import CASE_IDS, DATA, ROOT
from scripts.build_manufacturing_gold_100_v2 import rows, read, write
from scripts.report_manufacturing_gold_live import digest, reconcile, public_answer


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    run=args.run_dir;out=args.output
    records=read(run/'evaluation-results.json');finished=read(run/'evaluation-finished.json')
    reviews=read(run/'assistant-review.json');bindings=read(run/'bindings.json')
    if finished['executed'] != 7 or finished['stopped'] or [r['case_id'] for r in records] != list(CASE_IDS):
        raise ValueError('Only the completed single seven-case batch may be released here.')
    cases={c['id']:c for c in rows(DATA/'cases.jsonl')}
    if [r['case_id'] for r in reviews] != list(CASE_IDS):
        raise ValueError('Seven explicit non-independent reviews are required.')
    before={e['id'] for e in read(run/'evaluation-ledger-before.json')}
    new=[e for e in read(run/'evaluation-ledger-after.json') if e['id'] not in before]
    usage=reconcile(records,new)
    if (usage['calls'],usage['budget_tokens'],usage['unknown_calls']) != tuple(finished['budget'][k] for k in ('calls','charged_tokens','unknown_calls')):
        raise ValueError('Trace, independent ledger and runtime budget differ.')
    public=[]
    for raw,review in zip(records,reviews):
        case=cases[raw['case_id']]
        if (review['case_id'] != raw['case_id'] or review['raw_result_sha256'] != digest(run/(raw['case_id']+'-result.json'))
                or [f['id'] for f in review['required_facts']] != [f['id'] for f in case['expected']['required_facts']]
                or review['independent'] or review['reviewer_type'] != 'assistant'):
            raise ValueError('Review provenance, required facts or raw result hash differ.')
        passed=(raw['contract']['passed'] and all(f['passed'] for f in review['required_facts'])
            and not review['forbidden_violations'] and not review['unsupported_additions'] and not raw['harness_error'])
        if review['passed'] != passed:
            raise ValueError('Review total differs from its individual checks.')
        public.append(dict(case_id=case['id'],question=case['question'],result=public_answer(raw['final']),
            contract=raw['contract'],harness_error=raw['harness_error'],review=review,
            model_calls=sum(e['model_calls'] for e in raw['events']),raw_result_sha256=review['raw_result_sha256']))
    summary=dict(schema_version='manufacturing-gold-seven-live-v3',dataset='manufacturing_gold_100_v3',
        case_ids=list(CASE_IDS),executed=7,contract_passed=sum(r['contract']['passed'] for r in records),
        assistant_non_independent_passed=sum(r['passed'] for r in reviews),
        cases_with_model_calls=sum(r['model_calls']>0 for r in public),zero_model_cases=sum(r['model_calls']==0 for r in public),
        independent_holdout=False,human_approved=0,old_scores_recomputed=False,old_results_combined=False,
        complete_hundred_case_result=False,business_source_revision='b741bd2',entry_revision=bindings['source_revision'],
        request_retries=0,continuations=False,index_calls=0,limits=bindings['limits'],usage=usage,
        started_at=finished['started_at'],finished_at=finished['finished_at'],elapsed_seconds=finished['budget']['elapsed_seconds'])
    audits=dict(preservation=read(run/'preservation-final.json'),preflight=read(run/'preflight.json'),
        after=read(run/'audit.json'),snapshots=read(run/'snapshot-proof.json'),runtime=read(run/'runtime-proof.json'))
    if audits['preflight']['budget']['calls'] or audits['after']['budget']['calls']:
        raise ValueError('Preflight/audit must remain zero-model.')
    write(out/'summary.json',summary);write(out/'reviews.json',reviews);write(out/'audits.json',audits)
    write(out/'results.jsonl',''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in public),raw=True)
    write(out/'manifest.json',dict(input_sha256=bindings['input_hashes'],bindings_sha256=digest(run/'bindings.json'),
        raw_results={r['case_id']:r['raw_result_sha256'] for r in public},
        files_sha256={f:digest(out/f) for f in ('summary.json','reviews.json','audits.json','results.jsonl')},
        labels_sent_to_model=False,credentials_included=False,old_scores_recomputed=False))
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == '__main__':
    main()
