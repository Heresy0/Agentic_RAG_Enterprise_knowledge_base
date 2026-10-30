"""Bind completed reviewer judgments to observed paired answers and contexts.

This tool imports judgments. It does not generate or infer semantic verdicts.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.evaluation.semantics import review_binding


def bind(directory, judgments_path, output_directory=None):
    frozen=json.loads((directory/'frozen-criteria.json').read_text(encoding='utf-8'))
    judgments=json.loads(judgments_path.read_text(encoding='utf-8'))
    files={'criteria':directory/'frozen-criteria.json',
        **{v:directory/v/'traces.jsonl' for v in ('full_parent','model_sections')}}
    if judgments.get('observations_sha256')!={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in files.items()}:
        raise ValueError('Judgments are not bound to these frozen observations')
    reviewer=judgments.get('reviewer','').strip()
    if not reviewer or not judgments.get('rubric'):
        raise ValueError('Reviewer and rubric required')
    cases={c['id']:c for c in frozen['cases']}
    if set(judgments['cases'])!=set(cases):
        raise ValueError('Judgments must cover exactly the frozen cases')
    focus=set(frozen.get('condition_focus_ids',[])) & cases.keys()
    output=dict(reviewer=reviewer,rubric=judgments['rubric'],variants={},
        limitation='Assistant judgments after observing outputs; not blinded human review or a held-out accuracy estimate.')
    prepared={}
    for variant in ('full_parent','model_sections'):
        traces=[json.loads(line) for line in (directory/variant/'traces.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
        observed={r['id']:r for r in traces}
        if len(observed)!=len(traces) or set(observed)!=set(cases):
            raise ValueError('Traces must cover exactly the frozen cases without duplicates')
        reviews=[];kinds=Counter();focus_kinds=Counter();failed=[];focus_failed=[]
        for identifier,case in cases.items():
            row=observed[identifier];judgment=judgments['cases'][identifier]
            expected_question=hashlib.sha256(case['question'].encode()).hexdigest()
            if row.get('question_hash')!=expected_question:
                raise ValueError('Trace question fingerprint differs from frozen criteria')
            if 'response' not in row or 'context' not in row.get('stages',{}):
                raise ValueError('Review requires an observed response and context')
            failures=judgment.get('fail',[])
            if not isinstance(failures,list) or set(failures)-{'full_parent','model_sections'}:
                raise ValueError('Unknown judgment arm')
            reason=judgment.get(variant,judgment.get('both',''))
            if not isinstance(reason,str) or not reason.strip():
                raise ValueError('Every judgment requires a reason')
            verdict='failed' if variant in failures else 'passed'
            if verdict=='failed':
                kind=('false_refusal' if case['answerable'] and not row['response']['answerable'] else
                    judgment.get('failure_category',{}).get(variant))
                if kind not in {'false_refusal','condition_omission','semantic_error','incomplete_answer','unsupported_answer'}:
                    raise ValueError('A delivered failed answer requires an explicit reviewer failure category')
                kinds[kind]+=1;failed.append(identifier)
            else:
                kind='correct_refusal' if not case['answerable'] else 'complete_answer'
                kinds[kind]+=1
            if identifier in focus:
                focus_kinds[kind]+=1
                if verdict=='failed':focus_failed.append(identifier)
            reviews.append(dict(id=identifier,binding=review_binding(case,row['response'],row['stages']['context']),
                verdict=verdict,reviewer=reviewer,reason=reason,category=kind))
        prepared[variant]=reviews
        passed=sum(r['verdict']=='passed' for r in reviews)
        output['variants'][variant]=dict(reviewed=len(reviews),passed=passed,failed=len(reviews)-passed,
            complete_semantic_accuracy=passed/len(reviews),categories=dict(kinds),failed_ids=failed,
            condition_focus=dict(cases=len(focus),categories=dict(focus_kinds),failed_ids=focus_failed))
    destination=output_directory or directory
    destinations=[destination/v/'assistant-reviews.jsonl' for v in prepared]+[destination/'assistant-review-summary.json']
    if any(p.exists() for p in destinations):
        raise ValueError('Existing bound reviews must not be overwritten')
    for variant,reviews in prepared.items():
        (destination/variant).mkdir(parents=True,exist_ok=True)
        with (destination/variant/'assistant-reviews.jsonl').open('x',encoding='utf-8') as stream:
            for review in reviews:stream.write(json.dumps(review,ensure_ascii=False)+'\n')
    (destination/'assistant-review-summary.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return output


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--judgments',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args(argv)
    print(json.dumps(bind(args.directory,args.judgments,args.output_dir),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
