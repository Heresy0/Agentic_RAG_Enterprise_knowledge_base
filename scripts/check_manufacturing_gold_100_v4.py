"""Check v4 scope, unchanged boundaries and provenance offline."""
import argparse
from collections import Counter
from copy import deepcopy
import json
import sys
from scripts.build_manufacturing_gold_100_v4 import BASE,DATA,ROOT,CASE_IDS,POLICY,revise,render,scope_reason
from scripts.build_manufacturing_gold_100_v2 import read,rows,digest,canonical,COUNTS
from scripts.check_manufacturing_gold_100 import require,verify_sources


def validate(cases,sources,reviews,manifest,lineage,policy,audit):
    before=rows(BASE/'cases.jsonl')
    require(cases==revise(before),'题目、对象、证据或范围修订不符')
    require(len(cases)==100 and Counter(c['category'] for c in cases)==COUNTS,'题量分类变化')
    require(sources==read(BASE/'sources.json') and policy==POLICY,'来源或复核口径变化')
    require(lineage['scope_revised_cases']==list(CASE_IDS) and lineage['scope_reviewed_cases']==100
        and not lineage['old_scores_recomputed'] and not lineage['independent_holdout'],'修订或成绩口径错误')
    changes=[dict(case_id=c['id'],old_case_sha256=digest(canonical(b)),new_case_sha256=digest(canonical(c))) for b,c in zip(before,cases) if b!=c]
    require(len(changes)==14 and len(lineage['changes'])==14,'修订数量错误')
    require(all(all(a[k]==v for k,v in e.items()) and a['reason'] for e,a in zip(changes,lineage['changes'])),'修订指纹错误')
    require(len(reviews)==len(audit)==100,'百题审核不全')
    for c,b,r,a in zip(cases,before,reviews,audit):
        require({k:v for k,v in c.items() if k!='expected'}=={k:v for k,v in b.items() if k!='expected'},'问句选择证据不得改变')
        require(all(c['expected'][k]==b['expected'][k] for k in ('behavior','answerable','http_status','forbidden_claims','grading')),'业务边界不得放宽')
        require(r['case_id']==a['case_id']==c['id'] and r['reviewer_type']=='assistant' and r['human_reviewer'] is None
            and not r['independent'] and not r['professional_factory_review'] and r['decision']=='accepted_as_candidate','不得伪造独立批准')
        require(r['rationale']==a['reason']==scope_reason(c) and r['reviewed_at'] and not a['human_approved']
            and not a['independent'] and a['reviewer_type']=='assistant','范围复核记录错误')
        require(a['action']==('scope_revised' if c['id'] in CASE_IDS else 'kept'),'范围动作错误')
        ev={e['id'] for e in c['evidence']}
        require(r['evidence_ids']==a['evidence_ids']==[e['id'] for e in c['evidence']],'复核关联断开')
        for bucket in ('required_facts','optional_facts'):
            facts=c['expected'].get(bucket,[])
            require(len({f['id'] for f in facts})==len(facts) and all(f['text'] and f['evidence_ids'] and set(f['evidence_ids'])<=ev for f in facts),'要点证据断开')
    require(manifest['dataset']=='manufacturing_gold_100_v4' and manifest['cases']==100 and manifest['assistant_reviewed_cases']==100
        and manifest['status']=='candidate_assistant_reviewed_non_independent','候选身份错误')
    require(all(manifest[k]==0 for k in ('human_approved_cases','professional_factory_approved_cases','platform_approved_cases','model_calls','executed_cases'))
        and not any(manifest[k] for k in ('executable','live_verified','old_results_combined')),'不得伪造执行或批准')
    return dict(cases=100,scope_reviewed=100,scope_revised=14,unchanged=86,
        required_facts=sum(len(c['expected']['required_facts']) for c in cases),optional_facts=sum(len(c['expected'].get('optional_facts',[])) for c in cases))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--probes',action='store_true');a=p.parse_args()
    def no_network(event,_):
        if event in ('socket.connect','socket.getaddrinfo'):raise RuntimeError('离线检查禁止网络')
    sys.addaudithook(no_network)
    m=read(DATA/'manifest.json');l=read(DATA/'lineage.json')
    for name,h in m['files_sha256'].items():require(digest((DATA/name).read_bytes())==h,'候选文件变化')
    for name,h in m['implementation_sha256'].items():require(digest((ROOT/name).read_bytes())==h,'实现变化')
    for name,h in l['base_files_sha256'].items():require(digest((BASE/name).read_bytes())==h,'旧v3文件变化')
    v=[rows(DATA/'cases.jsonl'),read(DATA/'sources.json'),rows(DATA/'reviews.jsonl'),m,l,read(DATA/'review_policy.json'),rows(DATA/'scope_audit.jsonl')]
    result=validate(*v);require((DATA/'REVIEW.md').read_text(encoding='utf-8')==render(v[0],v[1]['sources'],v[2]),'可读视图不符')
    result.update(verify_sources(v[1]['sources']))
    if a.probes:
        probes={
            '恢复未问状态':lambda x:x[0][53]['expected']['required_facts'].append(dict(id='F99',text='待审核',evidence_ids=['E01'])),
            '删除062必要签署':lambda x:x[0][61]['expected']['required_facts'][0].update(text='无需签署'),
            '截断002必要条件':lambda x:x[0][1]['expected']['required_facts'][2].update(text='不需全部合格'),
            '改写084金标':lambda x:x[0][83].update(question='改写'),
            '删除禁止推论':lambda x:x[0][43]['expected'].update(forbidden_claims=[]),
            '伪造人工批准':lambda x:x[3].update(human_approved_cases=1),
            '遗漏通过题审核':lambda x:x[6].pop(0),
            '重算旧成绩':lambda x:x[4].update(old_scores_recomputed=True),
            '放宽补充事实':lambda x:x[5].update(truthfulness='可无来源'),
        }
        result['damage_probes']={}
        for name,mutate in probes.items():
            d=deepcopy(v);mutate(d)
            try:validate(*d)
            except ValueError:result['damage_probes'][name]='rejected'
            else:raise ValueError('未拒绝损坏：'+name)
    result.update(model_calls=0,old_scores_recomputed=False)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
