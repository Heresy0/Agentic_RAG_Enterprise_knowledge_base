"""Build current evaluation views from byte-frozen archives; never call models."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=ROOT/'evals/archive'
CURRENT=ROOT/'evals/current'
GROUPS=(('main','主回归100题','xinghai_v4'),
        ('multihop','复杂多跳12题','xinghai_multihop'),
        ('completeness','首次回答完整性8题','first_pass_generalization'))


def sha(raw):return hashlib.sha256(raw).hexdigest()
def dumps(value):return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


def verify_archives(root=ROOT):
    archive=root/'evals/archive'
    manifest=json.loads((archive/'manifest.json').read_text(encoding='utf-8'))
    seen=set()
    for row in manifest['files']:
        path=(root/row['archived_path']).resolve()
        if not path.is_relative_to(archive.resolve()) or path in seen:
            raise ValueError('Invalid archive inventory')
        seen.add(path)
        if not path.is_file() or sha(path.read_bytes())!=row['sha256']:
            raise ValueError('Frozen archive changed: '+row['archived_path'])
    return manifest


def build_artifacts(root=ROOT,output=CURRENT):
    root,output=Path(root),Path(output)
    verify_archives(root)
    archive=root/'evals/archive'
    artifacts={};groups=[];all_cases=[];raw_parts=[]
    for group_id,name,source_name in GROUPS:
        source=archive/source_name/'single_turn.jsonl';raw=source.read_bytes()
        if not raw.endswith(b'\n'):raise ValueError('Source pack needs final newline')
        rows=[json.loads(l) for l in raw.decode('utf-8-sig').splitlines() if l.strip()]
        groups.append(dict(id=group_id,name=name,source=source.relative_to(root).as_posix(),
            source_dataset_sha256=sha(raw),case_ids=[r['id'] for r in rows],
            cases=len(rows),answerable=sum(r['answerable'] for r in rows),
            unanswerable=sum(not r['answerable'] for r in rows)))
        artifacts[output/'groups'/group_id/'single_turn.jsonl']=raw
        artifacts[output/'groups'/group_id/'README.md']=(f'# {name}\n\n'
            f'由冻结归档生成，共{len(rows)}题。题目与标注原样保留，请勿手工修改。\n\n'
            f'统一运行：`python scripts/evaluate.py system --group {group_id}`，其余参数见[套件说明](../../README.md)。\n'
            f'原始说明与附件见[归档](../../../archive/{source_name}/README.md)。\n').encode('utf-8')
        all_cases.extend(rows);raw_parts.append(raw)
    ids=[r['id'] for r in all_cases]
    questions=[''.join(r['question'].split()) for r in all_cases]
    if len(ids)!=120 or len(set(ids))!=120 or len(set(questions))!=120:
        raise ValueError('Duplicate or missing single-turn cases')
    combined=b''.join(raw_parts)
    artifacts[output/'single_turn.jsonl']=combined
    main=archive/'xinghai_v4'
    for filename in ('conversations.json','permissions.json'):
        artifacts[output/filename]=(main/filename).read_bytes()
    rules=(root/'evals/scoring_rules.json').read_bytes()
    artifacts[output/'scoring_rules.json']=rules
    artifacts[output/'groups/main/scoring_rules.json']=rules
    quality_policy=(root/'evals/quality_policy.json').read_bytes()
    if json.loads(quality_policy)['dataset_sha256']!=sha(combined):
        raise ValueError('Quality policy must bind the unchanged120-case dataset')
    artifacts[output/'quality_policy.json']=quality_policy
    # Preserve reference prose while rebinding the active scoring annotations.
    active_rules=json.loads(rules)
    references=json.loads((main/'ragas_references_v1.json').read_bytes())
    references.update(version='2026-10-16-reference-v1-r10',
                      scoring_rules_sha256=sha(rules),scorer_version=active_rules['version'])
    for row in references['cases']:
        original=next(case for case in all_cases if case['id']==row['id'])
        effective={**original,**active_rules['cases'].get(row['id'],{})}
        row['original_criteria_sha256']=sha(json.dumps(effective,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8'))
    artifacts[output/'groups/main/ragas_references_v1.json']=dumps(references)
    workload=(archive/'load/current_workload.jsonl').read_bytes()
    load_rows=[json.loads(l) for l in workload.decode('utf-8-sig').splitlines() if l.strip()]
    by_id={r['id']:r for r in all_cases}
    for row in load_rows:
        source=by_id[row['id']]
        if row['question']!=source['question'] or row['answerable']!=source['answerable']:
            raise ValueError('Load workload differs from source question')
    artifacts[output/'load_workload.jsonl']=workload
    conversations=json.loads(artifacts[output/'conversations.json'])
    permissions=json.loads(artifacts[output/'permissions.json'])
    suite=dict(version=1,kind='synthetic_regression_not_independent_blind_test',
        dataset_sha256=sha(combined),scoring_rules_sha256=sha(artifacts[output/'scoring_rules.json']),
        quality_policy_sha256=sha(quality_policy),
        groups=groups,single_turn_cases=120,answerable=104,unanswerable=16,
        conversations=dict(groups=len(conversations),turns=sum(len(r['turns']) for r in conversations),
                           sha256=sha(artifacts[output/'conversations.json'])),
        security=dict(cases=len(permissions['cases']),sha256=sha(artifacts[output/'permissions.json'])),
        load=dict(cases=len(load_rows),reuses_single_turn_cases=True,sha256=sha(workload)),
        independent_case_units=120+len(conversations)+len(permissions['cases']),
        check_steps=120+sum(len(r['turns']) for r in conversations)+len(permissions['cases']),
        aggregation='Report main, multihop and completeness separately; historical100 scores are not120 scores.')
    artifacts[output/'suite.json']=dumps(suite)
    for group in ('公司公共','人力资源部','技术部','平台研发部'):
        rows=[r for r in all_cases if r['source_groups']==[group]]
        artifacts[output/'by_scope'/f'{group}.jsonl']=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows).encode('utf-8')
    for name,rows in [('cross_scope',[r for r in all_cases if len(r['source_groups'])>1]),
                      ('unanswerable',[r for r in all_cases if not r['answerable']])]:
        artifacts[output/(name+'.jsonl')]=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows).encode('utf-8')
    questions_book=['# 统一120题题册','', '原问题与事实标注来自冻结归档；按原分组评分。','']
    answers_book=['# 统一120题原参考与事实标注','',
                  '本册保留原始标注；运行时另应用scoring_rules.json中的当前覆盖，不新增事实标准。','']
    for spec in groups:
        questions_book += ['## '+spec['name'],'']
        answers_book += ['## '+spec['name'],'']
        for case_id in spec['case_ids']:
            c=by_id[case_id]
            head=[f"### {c['id']} · {c['category']}",'',c['question'],'',
                  f"业务日期：{c.get('as_of','见原题')}；范围：{c.get('expected_scope','见原题')}。",'']
            questions_book+=head;answers_book+=head
            answers_book+=['原参考：'+c['reference_answer'],'','原必答事实：'+'；'.join(c['required_facts']),
                '','原可选事实：'+'；'.join(c.get('optional_facts') or []),'']
            answers_book += ['- '+r['document']+'：'+r['text'] for r in c['expected_evidence']]
            answers_book += ['','语义检查：'+'；'.join(c.get('semantic_checks',[])),'']
    artifacts[output/'测试题册.md']=('\n'.join(questions_book).rstrip()+'\n').encode('utf-8')
    artifacts[output/'答案与评分.md']=('\n'.join(answers_book).rstrip()+'\n').encode('utf-8')
    return artifacts,suite


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true')
    args=p.parse_args(argv);artifacts,suite=build_artifacts();changed=[]
    for path,raw in artifacts.items():
        if path.is_file() and path.read_bytes()==raw:continue
        changed.append(path.relative_to(ROOT).as_posix())
        if not args.check:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print(json.dumps(dict(status='out_of_date' if args.check and changed else 'verified' if args.check else 'built',
        changed=changed,single_turn_cases=suite['single_turn_cases'],groups={r['id']:r['cases'] for r in suite['groups']},
        model_calls=0),ensure_ascii=False))
    return int(args.check and bool(changed))


if __name__=='__main__':raise SystemExit(main())
