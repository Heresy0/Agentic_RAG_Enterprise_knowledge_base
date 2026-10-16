"""Group selection/reporting for the unified pack; source cases remain unchanged."""
import hashlib
import json

from .metrics import summarize_stages
from .reporting import quality_layers


def load_suite(directory,cases):
    path=directory/'suite.json'
    if not path.is_file():return None
    suite=json.loads(path.read_text(encoding='utf-8'))
    sha=lambda file:hashlib.sha256(file.read_bytes()).hexdigest()
    if suite.get('version')!=1 or suite['dataset_sha256']!=sha(directory/'single_turn.jsonl'):
        raise ValueError('Suite dataset fingerprint differs; rebuild/check the suite')
    if suite['scoring_rules_sha256']!=sha(directory/'scoring_rules.json'):
        raise ValueError('Suite scoring fingerprint differs')
    ids=[r['id'] for r in cases];group_ids=[];names=[]
    for group in suite['groups']:
        group_ids.extend(group['case_ids']);names.append(group['id'])
    if len(set(names))!=len(names) or len(group_ids)!=len(set(group_ids)) or group_ids!=ids:
        raise ValueError('Suite groups must partition all cases in source order')
    if any(suite[key]['sha256']!=sha(directory/filename) for key,filename in
           [('conversations','conversations.json'),('security','permissions.json'),('load','load_workload.jsonl')]):
        raise ValueError('Suite auxiliary fingerprint differs')
    return suite


def select_groups(cases,suite,groups):
    if not groups:return cases
    if suite is None:raise ValueError('--group requires a suite.json dataset')
    known={g['id'] for g in suite['groups']}
    if set(groups)-known:raise ValueError('Unknown group: '+','.join(sorted(set(groups)-known)))
    ids={i for g in suite['groups'] if g['id'] in groups for i in g['case_ids']}
    return [r for r in cases if r['id'] in ids]


def group_scores(quality,suite,selected_ids,layers):
    result={}
    for group in suite['groups']:
        ids=set(group['case_ids'])&set(selected_ids)
        if not ids:continue
        records=[r for r in quality.get('records',[]) if r['id'] in ids]
        scored=quality_layers(dict(records=records,stages=summarize_stages(records),
                                  evaluation_type=quality.get('evaluation_type')))
        result[group['id']]=dict(name=group['name'],selected_cases=len(ids),
            source_dataset_sha256=group['source_dataset_sha256'],layers={
                name:{key:value for key,value in report.items() if key!='records'}
                for name,report in scored.items() if name in layers})
    return result
