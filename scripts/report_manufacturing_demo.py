"""Score a complete fresh 98-case demo run. Reviewed criteria and raw criteria are reported separately."""
import argparse,hashlib,json
from collections import Counter
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.build_manufacturing_demo_profile import load_profile
from scripts.run_manufacturing_http_acceptance import LocalKeycloakSession
from backend.app.evaluation.scoring_rules import apply_scoring_rules
from backend.app.evaluation.metrics import score_answer,SCORER_VERSION
from backend.app.evaluation.retrieval_metrics import evidence_metrics, summarize_stages
from scripts.evaluate_answers import summarize_trace_timings
from backend.app.model_usage import summarize_usage

def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def check_demo_release(pack):
    """Freeze the report's criteria; this does not add a runtime QA gate."""
    release=read(pack/'demo_release.json')
    if release['scorer_version']!=SCORER_VERSION:
        raise ValueError('评分版本与冻结演示口径不同，请另建版本并保留旧记录')
    for relative,expected in release['files_sha256_lf'].items():
        actual=hashlib.sha256((ROOT/relative).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
        if actual!=expected:
            raise ValueError('冻结演示文件发生变化：'+relative)
    return release

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--collect-ledger',action='store_true');a=p.parse_args(argv)
    pack=ROOT/'evals/manufacturing_zh_lite_v1';release=check_demo_release(pack);profile=load_profile(pack/'profile.json')
    cases=[r for split in profile['splits']for r in map(json.loads,(pack/split/'single_turn.jsonl').read_text(encoding='utf-8').splitlines())]
    reviewed={r['id']:r for r in apply_scoring_rules(cases,pack/'scoring_rules.json')}
    traces={};split_ids={};trace_hashes={}
    for split in profile['splits']:
        path=a.run_dir/(split+'-http-r1')/'traces.jsonl';items=[json.loads(line)for line in path.read_text(encoding='utf-8').splitlines()if line]
        ids=[r['id']for r in items]
        if len(set(ids))!=len(ids)or set(ids)!=set(profile['splits'][split]['ids']):raise ValueError('须覆盖完整精简分组，不可漏题、重复或混入其他题目')
        traces.update({r['id']:r for r in items});split_ids[split]=set(ids);trace_hashes[split]=sha(path)
    records=[]
    for case in cases:
        t=traces[case['id']]
        if t['question_hash']!=hashlib.sha256(case['question'].encode()).hexdigest():raise ValueError('问题变化')
        group='behavior'if not case['answerable']else 'ocr'if case['id']in set(profile['ocr_passed'])|set(profile['ocr_known_failures'])else 'ordinary'
        row=dict(id=case['id'],group=group,question=case['question'],reference_answer=case['reference_answer'],request_id=t.get('request_id'),run_id=t.get('run_id'))
        if 'error_type'in t:row.update(status='error',error_type=t['error_type'],http_status=t.get('http_status'),original_pass=False,reviewed_pass=False)
        else:
            old=score_answer(case,t['response'],t['stages']['context']);new=score_answer(reviewed[case['id']],t['response'],t['stages']['context'])
            row.update(status='passed'if new['automatic_proxy_pass']else 'failed',response=t['response'],
                original_pass=old['automatic_proxy_pass'],reviewed_pass=new['automatic_proxy_pass'],metrics=new,
                final_context_evidence=evidence_metrics(case['expected_evidence'],t['stages']['context']),
                pipeline=t.get('pipeline'),ledger_event_ids=[e['event_id']for e in t['stages']['model_usage']])
        records.append(row)
    def summary(rows):
        return dict(total=len(rows),complete=sum(r['status']!='error'for r in rows),errors=sum(r['status']=='error'for r in rows),
            original_proxy_passed=sum(r['original_pass']for r in rows),reviewed_proxy_passed=sum(r['reviewed_pass']for r in rows))
    report=dict(**summary(records),scorer_version=SCORER_VERSION,demo_release=release['id'],
        release_sha256=sha(pack/'demo_release.json'),selection='reviewed_demo_regression_not_blind_test',
        splits={k:summary([r for r in records if r['id']in ids])for k,ids in split_ids.items()},
        groups={g:summary([r for r in records if r['group']==g])for g in ('ordinary','ocr','behavior')},
        profile_sha256=sha(pack/'profile.json'),rules_sha256=sha(pack/'scoring_rules.json'),trace_sha256=trace_hashes,
        qa_retries=0,reviewer='assistant_non_independent_automatic_proxy_not_semantic_accuracy')
    report['timings']=summarize_trace_timings(list(traces.values()))
    report['retrieval_metrics_sha256']=hashlib.sha256((ROOT/'backend/app/evaluation/retrieval_metrics.py').read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    report['context_metrics']=summarize_stages([
        dict(metrics=dict(context=row['final_context_evidence'])) for row in records
        if 'final_context_evidence' in row])['context']
    if a.collect_ledger:
        config=read(a.run_dir/'system_config.json');http=LocalKeycloakSession(a.run_dir/'tokens.json');events={}
        for kb in config['knowledge_bases'].values():
            cursor={}
            while True:
                r=http.get(config['base_url']+'/api/knowledge-bases/'+kb+'/model-usage',params=dict(limit=100,**cursor),timeout=20);r.raise_for_status();data=r.json()
                for e in data['items']:events[e['id']]=dict(**{k:v for k,v in e.items()if k!='id'},event_id=e['id'],persisted=True)
                cursor=data.get('next_cursor')
                if not cursor:break
        for row in records:
            if row['status']!='error':
                actual={e['event_id']for e in events.values()if e['request_id']==row['request_id']}
                if actual!=set(row['ledger_event_ids']):raise ValueError('观测与实际账本事件不一致')
        report['ledger_ids_match']=True;report['usage']=summarize_usage(list(events.values()))
        report['errors_by_stage']=[dict(request_id=e['request_id'],stage=e['stage'],error_type=e['details'].get('error_type'))for e in events.values()if e['status']!='success']
    a.output_dir.mkdir(parents=True,exist_ok=False)
    for name,value in (('summary.json',report),('answers.json',records),('failures.json',[r for r in records if r['status']!='passed'])):
        (a.output_dir/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k]for k in ('total','complete','errors','original_proxy_passed','reviewed_proxy_passed','groups')},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
