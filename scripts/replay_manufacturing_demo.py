"""Same recorded responses, separate reviewed scoring. This does not measure a new model or corpus run."""
import argparse,hashlib,json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.build_manufacturing_demo_profile import load_profile
from backend.app.evaluation.scoring_rules import apply_scoring_rules
from backend.app.evaluation.metrics import score_answer,SCORER_VERSION

def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--recorded-run',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args(argv)
    folder=ROOT/'evals/manufacturing_zh_lite_v1';profile=load_profile(folder/'profile.json')
    source_report=ROOT/'evals/manufacturing_closed_loop/reports/20261029-r1/summary.json';proof=read(source_report)
    cases=[r for split in profile['splits']for r in map(json.loads,(folder/split/'single_turn.jsonl').read_text(encoding='utf-8').splitlines())]
    revised={c['id']:c for c in apply_scoring_rules(cases,folder/'scoring_rules.json')};traces={}
    for split,name in (('development','development-http-r2'),('test','test-http-r1')):
        path=a.recorded_run/name/'traces.jsonl'
        if sha(path)!=proof['results'][split]['trace_sha256']:raise ValueError('历史观测指纹不符')
        traces.update({r['id']:r for r in map(json.loads,path.read_text(encoding='utf-8').splitlines())})
    records=[]
    for original in cases:
        row=traces[original['id']]
        if row['question_hash']!=hashlib.sha256(original['question'].encode()).hexdigest():raise ValueError('原问题不一致')
        result=dict(id=original['id'],question=original['question'],run_id=row.get('run_id'))
        if 'error_type'in row:result.update(error_type=row['error_type'],before_pass=False,reviewed_pass=False)
        else:
            old=score_answer(original,row['response'],row['stages']['context'])
            new=score_answer(revised[original['id']],row['response'],row['stages']['context'])
            result.update(response=row['response'],before=old,reviewed=new,before_pass=old['automatic_proxy_pass'],reviewed_pass=new['automatic_proxy_pass'])
        records.append(result)
    report=dict(evaluation_type='offline_same_responses_scoring_review_not_model_improvement',scorer_version=SCORER_VERSION,
        selection='all_90_non_ocr_plus_5_prior_passed_and_3_known_failed_ocr',total=len(records),
        original_proxy_passed=sum(r['before_pass']for r in records),reviewed_proxy_passed=sum(r['reviewed_pass']for r in records),
        scoring_corrected_ids=[r['id']for r in records if not r['before_pass']and r['reviewed_pass']],
        unresolved_ids=[r['id']for r in records if not r['reviewed_pass']],
        reviewer='assistant_non_independent',model_calls=0,prior_corpus_documents=18,new_profile_documents=16,
        limitation='历史回答来自原18份资料环境；重评分不是精简语料或新提示词的实测。',
        profile_sha256=sha(folder/'profile.json'),rules_sha256=sha(folder/'scoring_rules.json'),source_report_sha256=sha(source_report))
    a.output_dir.mkdir(parents=True,exist_ok=False)
    for name,value in (('summary.json',report),('records.json',records)):
        (a.output_dir/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
