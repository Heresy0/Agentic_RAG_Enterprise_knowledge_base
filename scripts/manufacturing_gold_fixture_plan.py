"""Freeze the transport plan without executing QA, models, or runtime mutations."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
GOLD=ROOT/'evals/manufacturing_gold_100_v1'
LIMITS={
    'index':dict(max_model_calls=128,max_tokens=1000000,max_seconds=1800,reservation_tokens=32000),
    'evaluation':dict(max_cases=100,max_model_calls=800,max_tokens=5000000,max_seconds=9000,reservation_tokens=32000),
}


def make_plan():
    cases=[json.loads(x) for x in (GOLD/'cases.jsonl').read_text(encoding='utf-8').splitlines()]
    rows=[]
    for c in cases:
        mode=c['test_mode']
        if mode=='integration_protocol_candidate':
            platform='mes'; route='conversation_protocol'; alias_resolution=True
        elif mode=='offline_document_candidate':
            platform='erp';route='knowledge_qa';alias_resolution=False
        elif c['category']=='conversation':
            platform='mes' if c['input']['selection'].get('source_system_id')=='openmes-hc' else 'erp'
            route='conversation_protocol';alias_resolution=False
        elif c['input']['selection']:
            platform='mes' if c['input']['selection'].get('source_system_id')=='openmes-hc' else 'erp'
            route='manufacturing_qa';alias_resolution=False
        else:
            platform='erp';route='knowledge_qa';alias_resolution=False
        rows.append(dict(case_id=c['id'],test_mode=mode,platform=platform,route=route,
                         knowledge_scope='expanded_chinese' if mode in {'offline_document_candidate','integration_protocol_candidate'} else 'legacy_manufacturing',
                         requires_alias_resolution=alias_resolution,
                         expected_behavior=c['expected']['behavior'],gold_http_status=c['expected']['http_status']))
    assert len(rows)==100 and len({r['case_id'] for r in rows})==100
    return dict(schema_version='manufacturing-gold-100-execution-plan-v1',
        gold_cases_sha256=hashlib.sha256((GOLD/'cases.jsonl').read_bytes()).hexdigest(),
        budget_origin='operator_safety_ceilings_within_user_authorized_single_100_case_run',limits=LIMITS,
        request_retries=0,continuations=False,old_results_combined=False,
        preparation_model_calls=0,counts=dict(Counter(r['route'] for r in rows)),cases=rows)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    plan=make_plan();a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f:json.dump(plan,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(cases=100,counts=plan['counts'],model_calls=0),ensure_ascii=False))


if __name__=='__main__':main()
