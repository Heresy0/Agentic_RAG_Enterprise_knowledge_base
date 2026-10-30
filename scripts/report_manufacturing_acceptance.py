"""Offline grouped scoring of immutable HTTP observations; optional read-only ledger collection.

Automatic proxy passes are not semantic accuracy. Failed sources remain in the
denominator. This report never rewrites questions, original files or old reports.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app.evaluation.metrics import SCORER_VERSION,score_answer,evidence_metrics
from backend.app.model_usage import summarize_usage
from scripts.run_manufacturing_http_acceptance import LocalKeycloakSession


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def score(split,path,ocr):
    source=ROOT/'evals/manufacturing_zh_v3'/split/'single_turn.jsonl'
    cases=[json.loads(line)for line in source.read_text(encoding='utf-8').splitlines() if line]
    traces=[json.loads(line)for line in path.read_text(encoding='utf-8').splitlines() if line]
    by_id={r['id']:r for r in traces}
    if len(traces)!=len(by_id) or set(by_id)!={c['id']for c in cases}:raise ValueError('观测必须逐题覆盖整个冻结分组，不可删题或重复')
    records=[]
    for case in cases:
        trace=by_id[case['id']]
        if trace['question_hash']!=hashlib.sha256(case['question'].encode()).hexdigest():raise ValueError('题目指纹变化')
        group='behavior' if not case['answerable'] else 'ocr' if set(case['required_source_documents'])&ocr else 'ordinary'
        row=dict(id=case['id'],group=group,question=case['question'],reference_answer=case['reference_answer'],
            expected_behavior=case.get('expected_behavior'),run_id=trace.get('run_id'),request_id=trace.get('request_id'))
        if 'error_type'in trace:
            row.update(status='source_unavailable' if trace['error_type']=='SourceUnavailable' else 'error',
                       error_type=trace['error_type'],blocked_sources=trace.get('blocked_sources',[]))
        else:
            response=trace['response'];context=trace['stages']['context'];metrics=score_answer(case,response,context)
            evidence=evidence_metrics(case['expected_evidence'],context,alternative_sets=case.get('accepted_evidence_sets',[]))
            row.update(status='passed' if metrics['automatic_proxy_pass'] else 'failed',response=response,metrics=metrics,
                final_context_evidence=evidence,context_items=len(context),latency_ms=trace['latency_ms'],
                pipeline_stages=sorted((trace.get('pipeline_observation')or{}).get('stages',{})),
                pipeline_complete=(trace.get('pipeline_observation')or{}).get('complete'),
                model_ledger_event_ids=[e['event_id']for e in trace['stages']['model_usage']])
        records.append(row)
    def summary(rows):
        counts=Counter(r['status']for r in rows)
        return dict(total=len(rows),answered_http=sum(r['status']in('passed','failed')for r in rows),
            automatic_proxy_passed=counts['passed'],automatic_proxy_failed=counts['failed'],
            source_unavailable=counts['source_unavailable'],errors=counts['error'],
            behavior_correct=sum(r.get('metrics',{}).get('behavior_correct') is True for r in rows))
    result=dict(**summary(records),groups={g:summary([r for r in records if r['group']==g])for g in ('ordinary','ocr','behavior')},
        dataset_sha256=sha(source),trace_sha256=sha(path),selected_ids=[r['id']for r in records])
    return result,records


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--collect-ledger',action='store_true')
    a=p.parse_args(argv);run=a.run_dir.resolve();run.relative_to(ROOT/'tmp');out=a.output_dir.resolve();out.relative_to(ROOT/'tmp')
    out.mkdir(parents=True,exist_ok=False)
    manifest=read(ROOT/'sample_docs/manufacturing_zh_v3/manifest.json')
    ocr={Path(r['file']).name for r in manifest['documents']if 'OCR必需'in r['features']}
    results={};records={}
    for split,name in (('development','development-http-r2'),('test','test-http-r1')):
        results[split],records[split]=score(split,run/name/'traces.jsonl',ocr)
    old=ROOT/'tmp/manufacturing-zh-v3-live-20261029-dev-r1/http-r1/traces.jsonl'
    if old.exists():results['baseline_replay_same_scorer'],_=score('development',old,ocr)
    report=dict(scorer_version=SCORER_VERSION,review_type='automatic_proxy_requires_independent_semantic_review',
        application_change_ref='d8fe232',corpus_manifest_sha256=sha(ROOT/'sample_docs/manufacturing_zh_v3/manifest.json'),
        results=results,vision_enabled=False,qa_request_retries=0,
        budget_scope='HTTP batches limit question count only; quality-worker demo separately enforces calls/tokens/time. No monetary cap is asserted.')
    if a.collect_ledger:
        config=read(run/'system_config.json');http=LocalKeycloakSession(run/'tokens.json');events={}
        for kb in config['knowledge_bases'].values():
            cursor={}
            while True:
                r=http.get(config['base_url']+'/api/knowledge-bases/'+kb+'/model-usage',params=dict(limit=100,**cursor),timeout=20)
                r.raise_for_status();payload=r.json()
                for item in payload['items']:
                    event=dict(**{k:v for k,v in item.items()if k!='id'},event_id=item['id'],persisted=True)
                    if item['id']in events and events[item['id']]!=event:raise ValueError('账本ID重复但内容不一致')
                    events[item['id']]=event
                cursor=payload.get('next_cursor')
                if not cursor:break
        ledger=list(events.values());report['ledger']=dict(total=summarize_usage(ledger),
            stages={stage:summarize_usage([e for e in ledger if e['stage']==stage])for stage in sorted({e['stage']for e in ledger})})
        for split,rows in records.items():
            request_ids={r['request_id']for r in rows if r.get('request_id')}
            observed={e['event_id']for e in ledger if e['request_id']in request_ids}
            captured={event_id for row in rows for event_id in row.get('model_ledger_event_ids',[])}
            if observed!=captured:raise ValueError('问答与实际用量账本ID不符')
            report['results'][split]['ledger_ids_match']=True
            report['results'][split]['model_usage']=summarize_usage([e for e in ledger if e['request_id']in request_ids])
        (out/'ledger-events.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for name,data in (('summary.json',report),('answers.json',records),('failures.json',{
            split:[r for r in rows if r['status']!='passed']for split,rows in records.items()})):
        (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({split:{k:row[k]for k in ('total','answered_http','automatic_proxy_passed','source_unavailable','errors')}for split,row in results.items()},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
