"""Compare two saved Ragas runs; require identical answers, context and judge settings."""
import argparse
from collections import Counter
import html
import json
from pathlib import Path
import statistics

METRICS=('factual_correctness','faithfulness')
FIXED_PROTOCOL=('dataset_sha256','source_report_sha256','source_traces_sha256','scoring_rules_sha256',
    'scorer_version','ragas_version','judge_model','judge_base_url','dependency_versions','thresholds',
    'factual_correctness','temperature','top_p','max_output_tokens','reasoning_effort','sdk_retries',
    'instructor_max_attempts','context_policy','refusal_policy','sample_ids','business_rag_calls')


def read_run(directory):
    read=lambda name:json.loads((directory/name).read_text(encoding='utf-8'))
    report=read('effective-report.json' if (directory/'effective-report.json').exists() else 'report.json')
    protocol=read('protocol.json')
    samples=[json.loads(l) for l in (directory/'samples.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    return report,protocol,samples


def compare(before,after):
    br,bp,bs=before;ar,ap,ass=after
    for key in FIXED_PROTOCOL:
        if bp.get(key)!=ap.get(key):raise ValueError('Judge/source protocol differs: '+key)
    indexes=[]
    for rows in (bs,ass,br['records'],ar['records']):
        index={r['id']:r for r in rows}
        if len(index)!=len(rows) or set(index)!={s['id'] for s in bs}:raise ValueError('Duplicate/missing comparison samples')
        indexes.append(index)
    sb,sa,rb,ra=indexes
    rows=[]
    for i in sb:
        for key in ('user_input','response','answerable','expected_answerable','original_status','original_metrics','retrieved_contexts'):
            if sb[i].get(key)!=sa[i].get(key):raise ValueError('Frozen sample changed: '+i+' '+key)
        if sa[i].get('original_reference',sb[i]['reference'])!=sb[i]['reference']:
            raise ValueError('Original reference differs: '+i)
        for record in (rb[i],ra[i]):
            if any(record[m]['status']!='ok' for m in METRICS):raise ValueError('Comparison requires valid metrics: '+i)
        row=dict(id=i,original_status=sb[i]['original_status'],expected_answerable=sb[i]['expected_answerable'],
            answerable=sb[i]['answerable'],before_screen=rb[i]['judge_screen_pass'],after_screen=ra[i]['judge_screen_pass'])
        for metric in METRICS:
            row[metric]=dict(before=rb[i][metric]['value'],after=ra[i][metric]['value'],
                delta=round(ra[i][metric]['value']-rb[i][metric]['value'],12))
        rows.append(row)
    groups={}
    for name,predicate in dict(all=lambda r:True,delivered_answers=lambda r:r['expected_answerable'] and r['answerable'],
            false_refusals=lambda r:r['expected_answerable'] and not r['answerable'],
            unanswerable=lambda r:not r['expected_answerable']).items():
        selected=[r for r in rows if predicate(r)]
        groups[name]=dict(cases=len(selected))
        for metric in METRICS:
            groups[name][metric]={phase:statistics.mean(r[metric][phase] for r in selected) if selected else None
                for phase in ('before','after','delta')}
    summary=dict(cases=len(rows),groups=groups,
        original_passed=sum(r['original_status']=='passed' for r in rows),
        before_screen_passed=sum(r['before_screen'] for r in rows),after_screen_passed=sum(r['after_screen'] for r in rows),
        before_disagreements=sum(r['before_screen']!=(r['original_status']=='passed') for r in rows),
        after_disagreements=sum(r['after_screen']!=(r['original_status']=='passed') for r in rows),
        screen_recovered=[r['id'] for r in rows if not r['before_screen'] and r['after_screen']],
        screen_regressed=[r['id'] for r in rows if r['before_screen'] and not r['after_screen']],
        before_usage=br['judge_usage'],after_usage=ar['judge_usage'],records=rows)
    for metric in METRICS:
        summary[metric+'_changes']={label:sum(test(r[metric]['delta']) for r in rows)
            for label,test in dict(increased=lambda x:x>1e-9,decreased=lambda x:x<-1e-9,unchanged=lambda x:abs(x)<=1e-9).items()}
    return summary


def render(summary,before,after):
    samples_before={s['id']:s for s in before[2]};samples_after={s['id']:s for s in after[2]}
    esc=lambda s:html.escape(str(s))
    blocks=[]
    for row in summary['records']:
        b=samples_before[row['id']];a=samples_after[row['id']]
        score=' / '.join(f"{row[m]['before']:.3f} → {row[m]['after']:.3f}" for m in METRICS)
        sections=dict(问题=b['user_input'],原参考=b['reference'],必答参考=a['reference'],
            可选补充='\n'.join(a.get('optional_reference_information',[])),固定最终答案=a['response'],
            差异复核=row.get('review',{}).get('analysis','本题未列入与原规则的筛查差异。'),
            固定实际上下文='\n\n'.join(a['retrieved_contexts']))
        content=''.join(f'<h3>{esc(title)}</h3><pre>{esc(text)}</pre>' for title,text in sections.items())
        blocks.append(f"<details><summary>{esc(row['id'])} · {esc(row['original_status'])} · F1 / 忠实度 {score} · 筛查 {row['before_screen']} → {row['after_screen']}</summary>{content}</details>")
    overview=json.dumps({k:v for k,v in summary.items() if k!='records'},ensure_ascii=False,indent=2)
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>100题参考补全Ragas对照</title><style>body{max-width:1100px;margin:40px auto;font:16px/1.6 system-ui;padding:0 20px}details{border:1px solid #ccc;padding:12px;margin:12px 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:14px}h3{margin-bottom:4px}</style><h1>100题参考补全Ragas对照</h1><p>仅参考表述变化；答案、问题、上下文、原规则、裁判模型与参数一致。F1为连续诊断分，不是业务准确率；忠实度变化反映相同输入下裁判波动。可选补充不进入必答参考，原生F1仍可能扣减正确额外解释。</p><pre>'+esc(overview)+'</pre>'+''.join(blocks)+'</html>'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('before',type=Path);p.add_argument('after',type=Path);p.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args();before=read_run(args.before);after=read_run(args.after);summary=compare(before,after)
    review_path=args.after/'disagreement-review.json'
    if review_path.exists():
        reviews=json.loads(review_path.read_text(encoding='utf-8'))
        disagreements={r['id'] for r in summary['records'] if r['after_screen']!=(r['original_status']=='passed')}
        if set(reviews)!=disagreements:raise ValueError('Review set differs from remaining disagreements')
        for row in summary['records']:
            if row['id'] in reviews:row['review']=reviews[row['id']]
        summary['assistant_review_categories']=dict(Counter(r['category'] for r in reviews.values()))
        summary['assistant_review_note']='Assistant diagnosis of discrepancies; not independent human truth labels.'
    args.output_dir.mkdir(exist_ok=True,parents=True)
    (args.output_dir/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (args.output_dir/'comparison.html').write_text(render(summary,before,after),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='records'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
