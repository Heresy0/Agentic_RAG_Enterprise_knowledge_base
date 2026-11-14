"""Isolated prompt candidate, fixed model/context/schema, one bounded paid batch."""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.compare_manufacturing_generation_models import hashed
from scripts.measure_manufacturing_generation_stability import read,digest,write,load_source,frozen_context

MODEL='qwen3.7-flash'
VARIANTS=('current','candidate')
LIMITS=dict(max_model_calls=54,max_tokens=2000000,max_seconds=5400,reservation_tokens=32000)
PRIOR=ROOT/'evals/manufacturing_generation_stability_v1/runs/20261114-r15'
SOURCE=ROOT/'tmp/manufacturing-answer-path-full-live-20261114-r12'
CANDIDATE=ROOT/'evals/manufacturing_prompt_candidate_v1/candidate-system.txt'
MARKER='\n本次回答要求（不改变必答项、权限与最终校验）：\n'


def candidate_payload(payload):
    candidate=copy.deepcopy(payload)
    system=candidate['messages'][0]
    assert system['role']=='system' and system['content'].count(MARKER)==1
    _,contract=system['content'].split(MARKER)
    system['content']=CANDIDATE.read_text(encoding='utf-8').strip()+MARKER+contract
    return candidate


def freeze(directory):
    entries=[];controls=[]
    for old in read(PRIOR/'inputs.json'):
        payload=old['payload']
        assert payload['model']==MODEL and payload['extra_body']=={'enable_thinking':False}
        if MARKER not in payload['messages'][0]['content']:
            controls.append(dict(case_id=old['case_id'],unchanged_payload_sha256=hashed(payload)))
            continue
        candidate=candidate_payload(payload)
        unchanged=lambda p: {**p,'messages':p['messages'][1:]}
        assert unchanged(candidate)==unchanged(payload)
        assert candidate['messages'][0]['content'].split(MARKER)[1]==payload['messages'][0]['content'].split(MARKER)[1]
        entries.append(dict(case_id=old['case_id'],payloads=dict(current=payload,candidate=candidate),
            fixed_input_sha256=hashed(unchanged(payload)),
            system_characters={v:len(p['messages'][0]['content']) for v,p in [('current',payload),('candidate',candidate)]}))
    assert len(entries)==9 and len(controls)==3
    directory.mkdir(parents=True,exist_ok=False)
    write(directory,'inputs.json',entries)
    protected={p.relative_to(ROOT).as_posix():digest(p) for p in (ROOT/'backend/app').rglob('*.py')}
    for folder in (PRIOR,ROOT/'evals/manufacturing_gold_100_v4'):
        for p in folder.rglob('*'):
            if p.is_file():protected[p.relative_to(ROOT).as_posix()]=digest(p)
    for p in [CANDIDATE,Path(__file__),ROOT/'scripts/compare_manufacturing_generation_models.py',SOURCE/'evaluation-results.json']:
        protected[p.relative_to(ROOT).as_posix()]=digest(p)
    write(directory,'plan.json',dict(model=MODEL,variants=VARIANTS,repeats=3,limits=LIMITS,
        input_sha256=digest(directory/'inputs.json'),candidate_sha256=digest(CANDIDATE),protected=protected,
        source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
        source_directory=SOURCE.relative_to(ROOT).as_posix(),generation_cases=[e['case_id'] for e in entries],
        unchanged_manufacturing_controls=controls,scope='Only ordinary document system prompt replaced; dynamic contract preserved.',
        basis='All nine ordinary document cases from the preselected r13/r15 twelve; five r12 successes, four failures.',
        limitations='Known development cases, reconstructed historical headers, component experiment without live source checks, no holdout.',
        retries=0,repairs=0,retrieval_calls=0,labels_sent=False,gold100_score=False))
    print(json.dumps(dict(frozen_cases=9,unchanged_manufacturing_cases=3,calls=54,network_calls=0,
        system_characters=[dict(case=e['case_id'],**e['system_characters']) for e in entries])))


def run(directory,credentials):
    from dotenv import dotenv_values
    from openai import OpenAI,APIError
    from backend.app.model_usage import ModelCall,usage_context
    from backend.app.security.retrieval_scope import RetrievalScope
    from backend.app.quality.eval_budget import EvaluationBudget,evaluation_budget,EvaluationStopped
    plan=read(directory/'plan.json')
    assert plan['limits']==LIMITS and plan['model']==MODEL and plan['variants']==list(VARIANTS)
    assert digest(directory/'inputs.json')==plan['input_sha256']
    assert all(digest(ROOT/p)==h for p,h in plan['protected'].items())
    config=dotenv_values(credentials);assert config['DASHSCOPE_MODEL']==MODEL
    write(directory,'started.json',dict(at=datetime.now(timezone.utc).isoformat(),
        authorization='用户批准整理一版单独提示候选并真实对照；既有百炼目的地及资料范围。',limits=LIMITS))
    budget=EvaluationBudget(LIMITS);results=[];stopped=None
    client=OpenAI(api_key=config['DASHSCOPE_API_KEY'],base_url=config['DASHSCOPE_BASE_URL'],timeout=120,max_retries=0)
    scope=RetrievalScope(UUID('00000000-0000-0000-0000-000000000001'),UUID('00000000-0000-0000-0000-000000000002'))
    try:
        with evaluation_budget(budget):
            for repeat in range(1,4):
                for index,entry in enumerate(read(directory/'inputs.json')):
                    for variant in (VARIANTS if (repeat+index)%2 else VARIANTS[::-1]):
                        budget.check();assert digest(directory/'inputs.json')==plan['input_sha256']
                        payload=entry['payloads'][variant]
                        name=f"{entry['case_id']}-{variant}-{repeat}"
                        row=dict(case_id=entry['case_id'],model=MODEL,variant=variant,repeat=repeat,
                            fixed_input_sha256=entry['fixed_input_sha256'],request_sha256=hashed(payload))
                        write(directory,name+'-started.json',row)
                        with usage_context(scope=scope,request_id=name) as usage:
                            try:
                                with ModelCall('answer_generation',MODEL) as call:
                                    response=client.chat.completions.create(**payload)
                                    call.observe(response.usage.model_dump() if response.usage else None,response._request_id)
                                    choice=response.choices[0]
                                    row.update(content=choice.message.content,refusal=choice.message.refusal,
                                        finish_reason=choice.finish_reason,returned_model=response.model)
                                try:row['parsed']=json.loads(row['content'])
                                except (ValueError,TypeError):row['parse_error']='invalid_json'
                            except EvaluationStopped:raise
                            except Exception as error:
                                row['error_type']=type(error).__name__
                                if not isinstance(error,APIError):stopped='unexpected_error'
                                elif getattr(error,'status_code',None) in (400,401,403,404):stopped='parameter_or_access_error'
                            row['usage']=usage.events
                        if any((e.get('details',{}).get('reasoning_tokens') or 0)>0 for e in usage.events):stopped='thinking_mode_mismatch'
                        row['budget']=budget.snapshot();write(directory,name+'-result.json',row);results.append(row)
                        print(json.dumps(dict(case_id=row['case_id'],variant=variant,repeat=repeat,error=row.get('error_type'),budget=row['budget'])),flush=True)
                        if stopped:raise EvaluationStopped(stopped)
    except EvaluationStopped as error:stopped=str(error)
    finally:
        client.close();write(directory,'results.json',results)
        write(directory,'closed.json',dict(status='closed',at=datetime.now(timezone.utc).isoformat(),budget=budget.snapshot(),
            completed=len(results),stopped=stopped,pending=len(list(directory.glob('*-started.json')))-len(results),
            unused_allowance_reusable=False,retries=0,gold100_score=False))


def finish(directory):
    from backend.app.manufacturing.answer_context import manufacturing_answer_context
    from backend.app.knowledge.output_contract import live_generation_schema,validate_output
    from backend.app.knowledge.answer_service import AnswerService
    from backend.app.knowledge.answer_validation import AnswerValidator
    plan=read(directory/'plan.json');records,citations=load_source(ROOT/plan['source_directory'])
    cases={c['id']:c for c in map(json.loads,(ROOT/'evals/manufacturing_gold_100_v4/cases.jsonl').read_text(encoding='utf-8').splitlines())}
    observations=[]
    for row in read(directory/'results.json'):
        observation={k:row[k] for k in ('case_id','model','variant','repeat')}
        observation.update(error=row.get('error_type'),answer=None,valid=False)
        if isinstance(row.get('parsed'),dict):
            context,active=frozen_context(records[row['case_id']],cases[row['case_id']]['question'],citations)
            with manufacturing_answer_context(active):
                try:
                    draft=AnswerService._as_generation_draft(live_generation_schema(context).model_validate(row['parsed']),context)
                    result=validate_output(draft,validator=AnswerValidator(),context=context,question=cases[row['case_id']]['question'])
                    observation.update(draft=draft.model_dump(mode='json'),valid=result.valid,validation_errors=result.errors,
                        answer=(AnswerService._render_result(None,draft=draft,context=context,question=cases[row['case_id']]['question']).answer
                            if draft.answerable and result.valid else draft.refusal_reason))
                except (ValueError,TypeError) as error:observation['schema_error']=type(error).__name__
        observations.append(observation)
    assert all(digest(ROOT/p)==h for p,h in plan['protected'].items())
    write(directory,'observations.json',observations)
    print(json.dumps(dict(observations=len(observations),local_valid=sum(o['valid'] for o in observations),protected_inputs_unchanged=True,model_calls=0)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('freeze','run','finish'));parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--credentials',type=Path)
    args=parser.parse_args();directory=args.run_dir.resolve();directory.relative_to(ROOT/'tmp')
    if args.mode=='freeze':freeze(directory)
    elif args.mode=='run':run(directory,args.credentials.resolve())
    else:finish(directory)
