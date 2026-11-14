"""Offline frozen-draft and deterministic-delivery comparison; never run models."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from time import perf_counter
from unittest.mock import patch
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
# Installs process-wide connection/DNS denial before business imports.
from scripts.diagnose_manufacturing_answer_focus_regressions import frozen_context,manufacturing_answer_context
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.answer_models import EvidenceFirstAnswerDraft
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.output_contract import prepare_draft,validate_output,live_generation_schema
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.evidence_coordinator import CollectedEvidence
from backend.app.manufacturing.generation_selection import GroundedDraft
from backend.app.manufacturing.generation_models import precision_projection
from backend.app.manufacturing.answer_context import attach_business_context,facts_only_question,comparison_question
from backend.app.security.retrieval_scope import RetrievalScope
from backend.app.evaluation.trace import capture_trace,record_scope
from langchain_core.documents import Document

RUNS=('manufacturing-answer-contract-revised-full-live-20261113-r11',
      'manufacturing-answer-path-full-live-20261114-r12')
PUBLIC=('20261113-r11','20261114-r12')
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def local_observation(draft,context,question):
    validation=validate_output(draft,validator=AnswerValidator(),context=context,question=question)
    result=(AnswerService._render_result(None,draft=draft,context=context,question=question) if validation.valid and draft.answerable
            else AnswerService._unanswerable(draft) if validation.valid else None)
    return dict(draft=digest(draft.model_dump(mode='json')),valid=validation.valid,errors=validation.errors,
                result=digest(result.model_dump(mode='json')) if result else None)


def pipeline(question,context,active,draft,scope):
    """Pure saved first draft. Retrieval/model/feedback/repair are explicit stubs."""
    counts=dict(generation_function=0,model_stub=0,feedback=0,repair=0,source_precheck=0)
    service=object.__new__(AnswerService)
    service._settings=SimpleNamespace(chat_model='offline',rag_repair_enabled=True)
    service._validator=AnswerValidator()
    service._context_builder=SimpleNamespace(build=lambda *a,**k:context)
    docs=[Document(page_content=i.content,metadata=dict(tenant_id=str(scope.tenant_id),knowledge_base_id=i.knowledge_base_id)) for i in context.items]
    service._evidence_coordinator=SimpleNamespace(collect=lambda *a,**k:CollectedEvidence(docs))
    def feedback(*a,**k):counts['feedback']+=1;return None
    def repair(candidate,**k):counts['repair']+=1;return candidate
    service._evidence_feedback=SimpleNamespace(run=feedback)
    service._answer_repair=SimpleNamespace(repair=repair)
    service._base_llm=SimpleNamespace(with_structured_output=lambda *a,**k:None)
    original=service._generate_draft
    def generate(**k):counts['generation_function']+=1;return original(**k)
    service._generate_draft=generate
    def invoke(*a,**k):counts['model_stub']+=1;return draft.model_copy(deep=True)
    def source_check():counts['source_precheck']+=1
    selected=replace(active,before_generation=source_check) if active else None
    with manufacturing_answer_context(selected),capture_trace() as trace:
        record_scope(question,scope)
        with patch('backend.app.knowledge.answer_service.invoke_structured',invoke):
            result=service._answer(question,scope=scope,session=None,plan=None,planning_ms=None,total_started=perf_counter())
    value=result.model_dump(mode='json',exclude={'timings'})
    return dict(result=digest(value),answer=result.answer,answerable=result.answerable,response_type=result.response_type,
                counts=counts,generation_stages=[k for k in trace.stages if k.startswith('answer_generation')])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--compare',type=Path)
    args=p.parse_args();out=args.out.resolve();out.relative_to(ROOT/'tmp');out.parent.mkdir(parents=True,exist_ok=True)
    cases={c['id']:c for c in map(json.loads,(ROOT/'evals/manufacturing_gold_100_v4/cases.jsonl').read_text(encoding='utf-8').splitlines())}
    protected={}
    for folder in ('manufacturing_gold_100_v4','manufacturing_answer_contract_full_live_v1','manufacturing_generation_stability_v1'):
        for f in (ROOT/'evals'/folder).rglob('*'):
            if f.is_file():protected[f.relative_to(ROOT).as_posix()]=sha(f)
    rows=[];skipped=[];matrix=[]
    for run,pub in zip(RUNS,PUBLIC):
        grades={r['case_id']:r['review']['passed'] for r in map(json.loads,(ROOT/'evals/manufacturing_answer_contract_full_live_v1/runs'/pub/'results.jsonl').read_text(encoding='utf-8').splitlines())}
        for key,case in cases.items():
            path=ROOT/'tmp'/run/(key+'-result.json');protected[path.relative_to(ROOT).as_posix()]=sha(path);record=read(path)
            event=next((e for e in record['events'] if e['method']=='POST' and e['path'] in ('/api/qa','/api/manufacturing/qa')),None)
            if event is None:skipped.append(dict(run=run,case_id=key,reason='no_qa_post'));continue
            stages=event['trace']['stages'];generated=stages.get('answer_generation')
            if not generated:skipped.append(dict(run=run,case_id=key,reason='no_saved_generation_draft'));continue
            question=event['trace'].get('query') or case['question']
            context,active=frozen_context(dict(record,final=event),question)
            scope_data=event['trace']['scope'];scope=RetrievalScope(UUID(scope_data['tenant_id']),knowledge_base_ids=frozenset(map(UUID,scope_data['knowledge_base_ids'])))
            with manufacturing_answer_context(active):
                gen=generated[0];decision=next(d for d in stages['answer_decisions'] if d['stage']=='generation')
                body=gen['response'];cls=GroundedDraft if active else EvidenceFirstAnswerDraft
                draft=cls.model_validate(dict(answerable=decision['answerable'],response_type=body.get('response_type') or ('answer' if decision['answerable'] else 'refuse'),
                    refusal_reason=decision.get('reason'),claims=gen['claims'],coverage=gen['coverage']['coverage'],
                    missing_fields=body.get('missing_fields',[]),clarification_questions=body.get('clarification_questions',[])))
                draft=prepare_draft(draft,context)
                model_context=attach_business_context(context,question=question) if active else context
                projected=precision_projection(question,context) if active else None
                row=dict(run=run,case_id=key,question=question,original_case_passed=grades[key],
                    setup_turn_only=case['category']=='conversation',saved=local_observation(draft,context,question),
                    messages=digest([(type(m).__name__,m.content) for m in AnswerService._generation_messages(None,question,model_context)]),
                    schema=digest(live_generation_schema(model_context).model_json_schema()),deterministic=projected is not None)
                row['pipeline']=pipeline(question,context,active,draft,scope)
                row['pipeline_equals_recorded_answer']=row['pipeline']['answer']==event['response'].get('answer')
                rows.append(row)
            if active:
                questions=('工单实际开工时间是什么？','工序实际开工时间是什么？','作业卡实际开工时间是什么？',
                    '批次实际开工时间是什么？','步骤实际开工时间是什么？','所选卡何时开工？',
                    '所选工序的工序代码、登记状态和实际开工时间分别是什么？',
                    '所选作业卡的编号、状态和实际开工时间分别是什么？','实际开工几点几分？','工单和作业卡的实际开工分别是什么？')
                variants=[(active,q) for q in questions];source=active.context.record;execution=getattr(source,'execution',None)
                if execution is not None:
                    no_card=execution.model_copy(update={n:None for n in type(execution).model_fields if n.startswith('job_card_')})
                    variants.append((replace(active,context=active.context.model_copy(update={'record':source.model_copy(update={'execution':no_card})})),'作业卡实际开工时间是什么？'))
                if source.schema_version=='openmes-work-order-v1':
                    unselected=replace(active,context=active.context.model_copy(update={'record':source.model_copy(update={'batch':None,'step':None})}))
                    variants.extend((unselected,q) for q in ('批次实际开工时间是什么？','步骤实际开工时间是什么？'))
                for index,(candidate,q) in enumerate(variants):
                    candidate=replace(candidate,facts_only=facts_only_question(q),comparison=comparison_question(q))
                    with manufacturing_answer_context(candidate):
                        built=ContextBuilder().build([],question=q);projection=precision_projection(q,built)
                        value=dict(run=run,case_id=key,variant=index,question=q,selected=projection is not None)
                        if projection is not None:
                            value['local']=local_observation(prepare_draft(projection,built),built,q)
                            value['pipeline']=pipeline(q,built,candidate,projection,scope)
                        matrix.append(value)
    # Current r13 generated drafts are independent observations, not new grades here.
    from scripts.measure_manufacturing_generation_stability import frozen_context as stability_context,load_source
    directory=ROOT/'tmp/manufacturing-generation-stability-20261114-r13';source,citations=load_source(ROOT/'tmp'/RUNS[-1])
    for name in ('observations.json','results.json','plan.json'):
        f=directory/name;protected[f.relative_to(ROOT).as_posix()]=sha(f)
    for observation in read(directory/'observations.json'):
        if not observation.get('draft'):continue
        key=observation['case_id'];question=cases[key]['question'];context,active=stability_context(source[key],question,citations)
        with manufacturing_answer_context(active):
            cls=GroundedDraft if active else EvidenceFirstAnswerDraft;draft=cls.model_validate(observation['draft'])
            rows.append(dict(run='r13-component',case_id=key,repeat=observation['repeat'],
                saved=local_observation(prepare_draft(draft,context),context,question),
                messages=digest([(type(m).__name__,m.content) for m in AnswerService._generation_messages(None,question,context)]),
                schema=digest(live_generation_schema(context).model_json_schema()),deterministic=False))
    assert all(sha(ROOT/n)==h for n,h in protected.items())
    result=dict(rows=rows,matrix=matrix,skipped=skipped,protected=protected,model_calls=0,api_calls=0,runtime_database_calls=0,docker_operations=0)
    if args.compare:
        before=read(args.compare);assert before['protected']==protected and before['skipped']==skipped
        changes=[]
        for category in ('rows','matrix'):
            assert len(before[category])==len(result[category])
            for old,new in zip(before[category],result[category]):
                a=json.loads(json.dumps(old));b=json.loads(json.dumps(new))
                if 'pipeline' in a:
                    prior=a['pipeline'].pop('counts');now=b['pipeline'].pop('counts')
                    selected=new.get('deterministic',new.get('selected',False))
                    if selected:
                        assert prior['generation_function']==prior['feedback']==prior['repair']==1
                        assert now['generation_function']==now['feedback']==now['repair']==0
                        assert prior['model_stub']==now['model_stub']==0
                        changes.append(dict(category=category,case_id=new['case_id'],counts_before=prior,counts_after=now))
                    else:assert prior==now
                assert a==b,(category,new['case_id'],new.get('variant'))
        result['equivalent']=True;result['deterministic_pipeline_changes']=changes
    with out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(draft_records=len(rows),matrix=len(matrix),projected=sum(m['selected'] for m in matrix),
        original_success_drafts=sum(r.get('original_case_passed',False) for r in rows),skipped=len(skipped),protected=len(protected),equivalent=result.get('equivalent'),model_calls=0)))


if __name__=='__main__':main()
