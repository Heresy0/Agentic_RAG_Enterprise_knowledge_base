"""Offline before/after compatibility replay of closed r5/r8 traces.

Never executes QA, model calls, source APIs, databases or containers. Historical
answers/scores stay read-only; connection/DNS denial is installed before imports.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.diagnose_manufacturing_answer_focus_regressions import (
    read,digest,main_post,draft_for,check,manufacturing_answer_context,
)
from backend.app.knowledge.answer_models import EvidenceFirstAnswerDraft
from backend.app.manufacturing.generation_selection import GroundedDraft

RUNS={
    'r5':('manufacturing-gold-100-v4-grounded-live-20261113-r5','manufacturing_gold_100_grounded_live_v1'),
    'r8':('manufacturing-gold-100-v4-answer-focus-live-20261113-r8','manufacturing_gold_100_answer_focus_live_v1'),
}
CHANGED=('backend/app/knowledge/answer_models.py','backend/app/knowledge/answer_validation.py')
FIXED=('backend/app/knowledge/prompts.py','backend/app/manufacturing/generation_selection.py',
       'backend/app/knowledge/retrieval_service.py','backend/app/knowledge/rerank_retriever.py',
       'backend/app/knowledge/context_builder.py','backend/app/knowledge/context_selection.py',
       'backend/app/knowledge/parent_context_selection.py','backend/app/knowledge/answer_coverage.py',
       'backend/app/knowledge/output_contract.py','backend/app/knowledge/answer_repair.py',
       'backend/app/manufacturing/answer_context.py','backend/app/manufacturing/generation_models.py',
       'backend/app/manufacturing/source_statements.py')


def delivered_draft(record,context,initial):
    """Use final manufacturing bindings, or the existing local citation repair.

    Reuses first coverage only when it is unchanged/structurally sound; all
    successful cases must validate, otherwise replay is incomplete and fails.
    """
    candidate=initial.model_copy(deep=True)
    bindings=record['final']['response'].get('claim_bindings')
    if bindings:
        data=candidate.model_dump()
        data['claims']=[dict(text=b['text'],claim_kind=b.get('claim_kind'),
            citations=b['document_citations']+b['business_citations']) for b in bindings]
        candidate=GroundedDraft.model_validate(data)
    else:
        routing=main_post(record)['trace']['stages'].get('rag_routing',[])
        if any(r.get('kind')=='repair' and r.get('outcome')=='local_repaired' for r in routing):
            allowed={x.citation_id for x in [*context.items,*context.business_items]}
            for claim in candidate.claims:
                refs=[v.strip().strip('[]【】').strip() for v in claim.citations]
                assert refs and all(v in allowed for v in refs)
                claim.citations=list(dict.fromkeys(refs))
    return candidate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('before','after'),required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();out=args.out.resolve();out.relative_to(ROOT/'tmp')
    out.mkdir(exist_ok=True)
    target=out/(args.phase+'.json');assert not target.exists()
    gold=ROOT/'evals/manufacturing_gold_100_v4'
    cases={c['id']:c for c in map(json.loads,(gold/'cases.jsonl').read_text(encoding='utf-8').splitlines())}
    protected=list(gold.glob('*'))
    protected=[p for p in protected if p.is_file()]
    for private,public in RUNS.values():
        run=ROOT/'tmp'/private
        protected += [run/(key+suffix) for key in cases for suffix in ('-result.json','-stdout.log','-stderr.log')]
        protected += [p for p in (ROOT/'evals'/public/'runs'/('20261113-'+('r5' if 'r5' in private else 'r8'))).glob('*') if p.is_file()]
    protected += [ROOT/p for p in FIXED]
    before={str(p.relative_to(ROOT)).replace('\\','/'):digest(p) for p in protected}
    rows=[];no_generation=[]
    for tag,(private,public) in RUNS.items():
        run=ROOT/'tmp'/private
        scores={r['case_id']:r for r in map(json.loads,(ROOT/'evals'/public/'runs'/('20261113-'+tag)/'results.jsonl').read_text(encoding='utf-8').splitlines())}
        for key,case in cases.items():
            record=read(run/(key+'-result.json'))
            posts=[(i,e) for i,e in enumerate(record['events']) if e['method']=='POST' and e['path'] in ('/api/qa','/api/manufacturing/qa')]
            if not any(e['trace']['stages'].get('answer_generation') for _,e in posts):
                no_generation.append(dict(run=tag,case_id=key,successful=scores[key]['review']['passed'],reason='no_saved_generation_draft'))
                continue
            successful=scores[key]['review']['passed']
            for index,(event_index,event) in enumerate(posts):
                if not event['trace']['stages'].get('answer_generation'):continue
                end=posts[index+1][0] if index+1<len(posts) else len(record['events'])
                view=dict(record,final=event,events=record['events'][event_index:end])
                question=event['trace']['query']
                try:
                    context,active,draft,gen=draft_for(view,question)
                except (IndexError,StopIteration) as exc:
                    assert not successful,(tag,key,index,type(exc).__name__)
                    no_generation.append(dict(run=tag,case_id=key,post_index=index,successful=False,reason='no_saved_manufacturing_snapshot'))
                    continue
                with manufacturing_answer_context(active):
                    row=dict(run=tag,case_id=key,post_index=index,previous_successful=successful,
                             first=check(draft,context,question),
                             claims_sha256=hashlib.sha256(json.dumps(gen['claims'],ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
                             parsed_missing_information=[r.missing_information for r in draft.coverage])
                    if successful:
                        final=delivered_draft(view,context,draft)
                        row['delivered']=check(final,context,question)
                        assert row['delivered']['valid'],(tag,key,index,row['delivered'])
                    rows.append(row)
    result=dict(phase=args.phase,cases=rows,unreplayed=no_generation,
        successful_saved_drafts=sum(r['previous_successful'] for r in rows),
        successful_without_saved_drafts=sum(r['successful'] for r in no_generation),
        input_sha256=before,changed_code_sha256={p:digest(ROOT/p) for p in CHANGED},
        schema_sha256={cls.__name__:hashlib.sha256(json.dumps(cls.model_json_schema(),sort_keys=True).encode()).hexdigest()
                       for cls in (EvidenceFirstAnswerDraft,GroundedDraft)},
        model_calls=0,api_calls=0,database_calls=0,container_operations=0,network_connections_allowed=False,
        old_scores_recomputed=False,
        limitation='Saved processed drafts only, not new generations or live source/permission verification. Existing successful semantic scores remain historical, not newly approved.')
    assert all(digest(ROOT/name)==value for name,value in before.items())
    result['inputs_unchanged']=True
    if args.phase=='after':
        original=read(out/'before.json')
        assert original['input_sha256']==before
        assert original['schema_sha256']==result['schema_sha256']
        assert original['unreplayed']==no_generation
        prior={(r['run'],r['case_id'],r['post_index']):r for r in original['cases']}
        changes=[]
        for row in rows:
            old=prior[(row['run'],row['case_id'],row['post_index'])]
            assert old['claims_sha256']==row['claims_sha256']
            assert old.get('delivered')==row.get('delivered')
            if old['first']!=row['first']:
                changes.append(dict(run=row['run'],case_id=row['case_id'],before=old['first'],after=row['first']))
        assert {(r['run'],r['case_id']) for r in changes}=={('r8','MFG-GOLD-010'),('r8','MFG-GOLD-019')},changes
        assert all(r['after']['valid'] for r in changes)
        result['validation_changes']=changes
        result['successful_compatibility_unchanged']=True
    with target.open('x',encoding='utf-8',newline='\n') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(phase=args.phase,replayed=len(rows),successful_saved_drafts=result['successful_saved_drafts'],
                         successful_without_saved_drafts=result['successful_without_saved_drafts'],
                         protected_inputs=len(before),changes=result.get('validation_changes'),model_calls=0)))


if __name__=='__main__':main()
