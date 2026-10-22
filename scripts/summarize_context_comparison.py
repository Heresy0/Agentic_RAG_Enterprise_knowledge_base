"""Summarize a paired run without confusing proxies with semantic correctness."""
import argparse
import json
import math
from pathlib import Path
import statistics


def percentile(values, p):
    return sorted(values)[max(0, math.ceil(p*len(values))-1)] if values else None


def summarize(directory):
    protocol=json.loads((directory/'protocol.json').read_text(encoding='utf-8'))
    frozen=json.loads((directory/'frozen-criteria.json').read_text(encoding='utf-8'))
    cases={row['id']:row for row in frozen['cases']}
    output=dict(protocol=dict(kind=protocol['kind'],case_count=len(cases),budget=protocol.get('context_max_characters'),
        same_corpus=protocol.get('same_corpus'),same_permissions=protocol.get('same_permissions')),variants={})
    rows_by_variant={}
    for name in ('full_parent','model_sections'):
        path=directory/name/'traces.jsonl'
        rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.is_file() else []
        rows_by_variant[name]={row['id']:row for row in rows}
        events={event['event_id']:event for row in rows for event in row.get('stages',{}).get('model_usage',[])}
        responses=[row for row in rows if 'response' in row]
        answered=[row for row in responses if row['response']['answerable']]
        refused=[row for row in responses if not row['response']['answerable']]
        latencies=[row['latency_ms'] for row in rows]
        cost_by_stage={}; calls_by_stage={}; tokens_by_stage={}
        for event in events.values():
            stage=event['stage'];calls_by_stage[stage]=calls_by_stage.get(stage,0)+1
            if event.get('estimated_cost') is not None:cost_by_stage[stage]=cost_by_stage.get(stage,0)+float(event['estimated_cost'])
            if event.get('total_tokens') is not None:tokens_by_stage[stage]=tokens_by_stage.get(stage,0)+event['total_tokens']
        reasons={}
        for row in rows:
            for decision in row.get('stages',{}).get('context_selection',[]):
                for parent in decision.get('parent_selections',[]):
                    key=parent['reason'];reasons[key]=reasons.get(key,0)+1
        summary=dict(completed=len(rows),responses=len(responses),errors=sum('error_type' in row for row in rows),
            answered=len(answered),refused=len(refused),refusal_rate=len(refused)/len(responses) if responses else None,
            answerable_cases=sum(cases[row['id']]['answerable'] for row in rows),
            false_refusals=sum(cases[row['id']]['answerable'] for row in refused),
            correct_refusals=sum(not cases[row['id']]['answerable'] for row in refused),
            unsupported_answers=sum(not cases[row['id']]['answerable'] for row in answered),
            negative_cases=sum(not cases[row['id']]['answerable'] for row in rows),
            latency_ms=dict(mean=statistics.mean(latencies) if latencies else None,median=statistics.median(latencies) if latencies else None,p95=percentile(latencies,.95)),
            model_calls=len(events),selection_cases=sum(any(e.get('stage')=='context_selection' for e in row.get('stages',{}).get('model_usage',[])) for row in rows),
            calls_by_stage=calls_by_stage,tokens_by_stage=tokens_by_stage,
            total_tokens=sum(tokens_by_stage.values()),unknown_usage_calls=sum(e.get('total_tokens') is None for e in events.values()),
            estimated_cny=sum(cost_by_stage.values()),cost_by_stage=cost_by_stage,
            unpriced_calls=sum(e.get('estimated_cost') is None for e in events.values()),
            cost_complete=all(e.get('estimated_cost') is not None and e.get('total_tokens') is not None for e in events.values()),
            max_chat_input_tokens=max((e.get('input_tokens') or 0 for e in events.values() if e['stage'] not in ('query_embedding','rerank')),default=0),
            parent_selection_reasons=reasons)
        summary['incomplete_context_cases']=sum(any(s.get('incomplete_reason') for s in row.get('stages',{}).get('context_status',[])) for row in rows)
        intact=shortened=unverified=0
        for row in rows:
            originals={(d.get('knowledge_base_id'),d.get('chunk_id'),d.get('parent_id')):d['content']
                for d in row.get('stages',{}).get('authorized_final',[]) if d.get('parent_id')}
            for item in row.get('stages',{}).get('context',[]):
                if not item.get('parent_id'):continue
                original=originals.get((item.get('knowledge_base_id'),item.get('chunk_id'),item.get('parent_id')))
                if original is None:unverified+=1
                elif item['content'].strip()==original.strip():intact+=1
                else:shortened+=1
        summary['final_parent_items']=dict(intact=intact,changed=shortened,unverified=unverified)
        report_path=directory/name/'report.json'
        if report_path.is_file():
            report=json.loads(report_path.read_text(encoding='utf-8'))
            summary['legacy_proxy']=report['layers']['answer']['summary']
            summary['bounded_semantics']=report['layers']['semantics']['summary']
        output['variants'][name]=summary
    pairs=[]
    for identifier in cases:
        a=rows_by_variant['full_parent'].get(identifier);b=rows_by_variant['model_sections'].get(identifier)
        if not a or not b:continue
        def documents(row,stage):
            return [(r.get('knowledge_base_id'),r.get('chunk_id'),r.get('parent_id'),r.get('content')) for r in row.get('stages',{}).get(stage,[])]
        def equal_stage(stage):
            return (documents(a,stage)==documents(b,stage)
                if all(stage in r.get('stages',{}) for r in (a,b)) else None)
        responses_observed=all('response' in r for r in (a,b))
        pairs.append(dict(id=identifier,same_initial_evidence=equal_stage('parent_context'),
            same_final_context=equal_stage('context'),
            same_answer=(a['response']['answer']==b['response']['answer']) if responses_observed else None,
            same_answerability=(a['response']['answerable']==b['response']['answerable']) if responses_observed else None))
    output['pairs']=dict(completed=len(pairs),
        initial_evidence_observed_pairs=sum(p['same_initial_evidence'] is not None for p in pairs),
        final_context_observed_pairs=sum(p['same_final_context'] is not None for p in pairs),
        same_initial_evidence=sum(p['same_initial_evidence'] is True for p in pairs),
        same_final_context=sum(p['same_final_context'] is True for p in pairs),
        same_answerability=sum(p['same_answerability'] is True for p in pairs),
        changed_context_ids=[p['id'] for p in pairs if p['same_final_context'] is False],
        changed_answerability_ids=[p['id'] for p in pairs if p['same_answerability'] is False])
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    args=parser.parse_args();result=summarize(args.directory)
    (args.directory/'comparison-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
