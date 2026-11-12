"""Offline scope/lineage checks; never start the application or call a model."""
import argparse
from copy import deepcopy
import json
import sys

from scripts.build_manufacturing_gold_100_v3 import BASE, DATA, ROOT, CASE_IDS, POLICY, revise, render
from scripts.build_manufacturing_gold_100_v2 import read, rows, digest, canonical
from scripts.check_manufacturing_gold_100 import require, verify_sources


def validate(cases, sources, reviews, manifest, lineage, policy):
    before = rows(BASE/'cases.jsonl')
    require(cases == revise(before), '题目、对象、证据、边界或范围修订不符')
    require(sources == read(BASE/'sources.json'), '本轮不得修改来源证据')
    require(policy == POLICY, '复核口径不符')
    require(lineage['scope_revised_cases'] == list(CASE_IDS) and not lineage['old_scores_recomputed']
            and not lineage['independent_holdout'], '修订清单或旧成绩口径错误')
    changes = [dict(case_id=c['id'],old_case_sha256=digest(canonical(b)),new_case_sha256=digest(canonical(c)))
               for b,c in zip(before,cases) if b != c]
    require(len(changes) == 7 and len(lineage['changes']) == 7, '只能修订指定7题')
    for expected, actual in zip(changes,lineage['changes']):
        require(all(actual[k] == v for k,v in expected.items()) and actual['reason'], '修订指纹错误')
    old_reviews = rows(BASE/'reviews.jsonl')
    require(len(reviews) == 100, '复核数量错误')
    for c,r,b in zip(cases,reviews,old_reviews):
        require(r['case_id'] == c['id'] and r['evidence_ids'] == [e['id'] for e in c['evidence']], '复核关联错误')
        require(r['reviewer_type'] == 'assistant' and r['human_reviewer'] is None and not r['independent']
                and not r['professional_factory_review'] and r['decision'] == 'accepted_as_candidate', '不得伪造独立批准')
        require(r['rationale'] and r['reviewed_at'], '复核依据缺失')
        if c['id'] not in CASE_IDS:
            require(r == b, '无关复核被改写')
        evidence = {e['id'] for e in c['evidence']}
        for bucket in ('required_facts','optional_facts'):
            facts = c['expected'].get(bucket,[])
            require(len({f['id'] for f in facts}) == len(facts), '要点编号重复')
            require(all(f['text'] and f['evidence_ids'] and set(f['evidence_ids']) <= evidence for f in facts), '要点证据断开')
    require(manifest['dataset'] == 'manufacturing_gold_100_v3' and manifest['cases'] == 100
            and manifest['assistant_reviewed_cases'] == 100 and manifest['status'] == 'candidate_assistant_reviewed_non_independent', '候选身份错误')
    require(all(manifest[k] == 0 for k in ('human_approved_cases','professional_factory_approved_cases','platform_approved_cases','model_calls','executed_cases'))
            and not any(manifest[k] for k in ('executable','live_verified','old_results_combined')), '不得伪造执行或批准')
    return dict(cases=100,scope_revised=7,unchanged=93,required_facts=sum(len(c['expected']['required_facts']) for c in cases),
                optional_facts=sum(len(c['expected'].get('optional_facts',[])) for c in cases))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--probes',action='store_true');args=parser.parse_args()
    def no_network(event, _):
        if event in ('socket.connect','socket.getaddrinfo'):
            raise RuntimeError('离线检查禁止网络')
    sys.addaudithook(no_network)
    manifest=read(DATA/'manifest.json');lineage=read(DATA/'lineage.json')
    for path,h in manifest['files_sha256'].items():
        require(digest((DATA/path).read_bytes()) == h, '候选文件变化')
    for path,h in manifest['implementation_sha256'].items():
        require(digest((ROOT/path).read_bytes()) == h, '构建或检查器变化')
    for path,h in lineage['base_files_sha256'].items():
        require(digest((BASE/path).read_bytes()) == h, '旧v2文件变化')
    values=[rows(DATA/'cases.jsonl'),read(DATA/'sources.json'),rows(DATA/'reviews.jsonl'),manifest,lineage,read(DATA/'review_policy.json')]
    result=validate(*values)
    require((DATA/'REVIEW.md').read_text(encoding='utf-8') == render(values[0],values[1]['sources'],values[2]), '可读视图不符')
    result.update(verify_sources(values[1]['sources']))
    if args.probes:
        def require_status(v): v[0][18]['expected']['required_facts'].append(dict(id='F99',text='Open',evidence_ids=['E02']))
        probes={
            '恢复未问状态':require_status,
            '移除禁止结论':lambda v:v[0][5]['expected'].update(forbidden_claims=[]),
            '改变无关084':lambda v:v[0][83].update(question='改写'),
            '改写引用证据':lambda v:v[0][63]['evidence'][0].update(quote='伪造'),
            '伪造人工批准':lambda v:v[3].update(human_approved_cases=1),
            '重算旧成绩':lambda v:v[4].update(old_scores_recomputed=True),
            '放宽补充事实':lambda v:v[5].update(truthfulness='可选项允许无来源'),
        }
        result['damage_probes']={}
        for name,mutate in probes.items():
            damaged=deepcopy(values);mutate(damaged)
            try: validate(*damaged)
            except ValueError: result['damage_probes'][name]='rejected'
            else: raise ValueError('损坏探针未拒绝：'+name)
    result.update(model_calls=0,old_scores_recomputed=False)
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
