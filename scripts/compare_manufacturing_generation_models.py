"""One bounded, paired model comparison on previously frozen generation inputs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.measure_manufacturing_generation_stability import read, digest, write, load_source, frozen_context

MODELS = ('qwen3.7-flash', 'qwen3.8-flash')
LIMITS = dict(max_model_calls=72, max_tokens=3000000, max_seconds=7200, reservation_tokens=32000)
SOURCE = ROOT/'tmp/manufacturing-answer-path-full-live-20261114-r12'
PRIOR = ROOT/'evals/manufacturing_generation_stability_v1/runs/20261114-r13'


def hashed(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def freeze(directory):
    import httpx
    from langchain_openai import ChatOpenAI
    from backend.app.knowledge.answer_service import AnswerService
    from backend.app.knowledge.output_contract import live_generation_schema
    from backend.app.manufacturing.answer_context import manufacturing_answer_context
    records, citations = load_source(SOURCE)
    gold = {c['id']: c for c in map(json.loads, (ROOT/'evals/manufacturing_gold_100_v4/cases.jsonl').read_text(encoding='utf-8').splitlines())}
    entries = [e for e in read(PRIOR/'inputs.json') if e['kind']=='generation']
    for entry in entries:
        context, active = frozen_context(records[entry['case_id']], gold[entry['case_id']]['question'], citations)
        captured = []
        def intercept(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=dict(id='freeze', choices=[dict(index=0, finish_reason='stop',
                message=dict(role='assistant', content=json.dumps(dict(answerable=False,response_type='refuse',
                    claims=[],refusal_reason='freeze',coverage=[dict(requirement_id='Q1',aspect='freeze',
                    status='insufficient',claim_indices=[],missing_information='freeze')]))))],
                created=0, model='frozen', object='chat.completion'))
        with httpx.Client(transport=httpx.MockTransport(intercept)) as client, manufacturing_answer_context(active):
            model = ChatOpenAI(model=MODELS[0], base_url='https://unused.invalid/v1', api_key='freeze-only',
                temperature=0, reasoning_effort='none', timeout=120, max_retries=0, http_client=client, http_socket_options=())
            model.with_structured_output(live_generation_schema(context), include_raw=True).invoke(
                AnswerService._generation_messages(None, gold[entry['case_id']]['question'], context))
        assert captured == [entry['payload']], entry['case_id']
        entry['historical_request_sha256'] = entry.pop('request_sha256')
        entry['payload']['extra_body'] = {'enable_thinking': False}
        entry['shared_input_sha256'] = hashed({k:v for k,v in entry['payload'].items() if k!='model'})
    assert len(entries)==12
    directory.mkdir(parents=True, exist_ok=False)
    write(directory, 'inputs.json', entries)
    protected = {p.relative_to(ROOT).as_posix(): digest(p) for p in (ROOT/'backend/app').rglob('*.py')}
    for folder in (ROOT/'evals/manufacturing_gold_100_v4', PRIOR):
        for p in folder.rglob('*'):
            if p.is_file(): protected[p.relative_to(ROOT).as_posix()] = digest(p)
    for entry in entries:
        p = SOURCE/(entry['case_id']+'-result.json')
        protected[p.relative_to(ROOT).as_posix()] = digest(p)
    protected[(SOURCE/'evaluation-results.json').relative_to(ROOT).as_posix()] = digest(SOURCE/'evaluation-results.json')
    protected[Path(__file__).relative_to(ROOT).as_posix()] = digest(Path(__file__))
    write(directory, 'plan.json', dict(models=MODELS, repeats=3, limits=LIMITS, input_sha256=digest(directory/'inputs.json'),
        source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
        protected=protected, source_directory=SOURCE.relative_to(ROOT).as_posix(),
        generation_cases=[e['case_id'] for e in entries],
        thinking='Explicit enable_thinking=false for both arms; retain original reasoning_effort=none.',
        basis='Same preselected r13 twelve cases: six r12 successes and six failures. No selection using new outputs.',
        limitations='Historical reconstructed headers, no fresh source/permission checks; component comparison, not gold100 or holdout.',
        retries=0, repairs=0, retrieval_calls=0, labels_sent=False, gold100_score=False))
    print(json.dumps(dict(frozen_cases=len(entries), scheduled_calls=72, current_payloads_equal_prior=True, network_calls=0)))


def run(directory, credentials):
    from dotenv import dotenv_values
    from openai import OpenAI, APIError
    from backend.app.model_usage import ModelCall, usage_context
    from backend.app.security.retrieval_scope import RetrievalScope
    from backend.app.quality.eval_budget import EvaluationBudget, evaluation_budget, EvaluationStopped
    plan = read(directory/'plan.json')
    assert plan['limits']==LIMITS and plan['models']==list(MODELS)
    assert digest(directory/'inputs.json')==plan['input_sha256']
    assert all(digest(ROOT/p)==h for p,h in plan['protected'].items())
    config = dotenv_values(credentials)
    assert config['DASHSCOPE_MODEL']==MODELS[0]
    write(directory, 'started.json', dict(at=datetime.now(timezone.utc).isoformat(),
        authorization='用户要求先对照现有模型与qwen3.8-flash；既有百炼目的地及相同资料范围。', limits=LIMITS))
    budget = EvaluationBudget(LIMITS)
    client = OpenAI(api_key=config['DASHSCOPE_API_KEY'], base_url=config['DASHSCOPE_BASE_URL'], timeout=120, max_retries=0)
    scope = RetrievalScope(UUID('00000000-0000-0000-0000-000000000001'), UUID('00000000-0000-0000-0000-000000000002'))
    results, stopped = [], None
    try:
        with evaluation_budget(budget):
            for repeat in range(1, 4):
                for index, entry in enumerate(read(directory/'inputs.json')):
                    order = MODELS if (repeat+index)%2 else MODELS[::-1]
                    for model in order:
                        budget.check()
                        assert digest(directory/'inputs.json')==plan['input_sha256']
                        payload = dict(entry['payload'], model=model)
                        assert hashed({k:v for k,v in payload.items() if k!='model'})==entry['shared_input_sha256']
                        name=f"{entry['case_id']}-{model}-{repeat}"
                        row=dict(case_id=entry['case_id'], model=model, repeat=repeat, kind='generation',
                            shared_input_sha256=entry['shared_input_sha256'], request_sha256=hashed(payload))
                        write(directory, name+'-started.json', row)
                        with usage_context(scope=scope, request_id=name) as usage:
                            try:
                                with ModelCall('answer_generation', model) as call:
                                    response=client.chat.completions.create(**payload)
                                    call.observe(response.usage.model_dump() if response.usage else None, response._request_id)
                                    choice=response.choices[0]
                                    row.update(content=choice.message.content, refusal=choice.message.refusal,
                                        finish_reason=choice.finish_reason, returned_model=response.model)
                                try: row['parsed']=json.loads(row['content'])
                                except (ValueError, TypeError): row['parse_error']='invalid_json'
                            except EvaluationStopped:
                                raise
                            except Exception as error:
                                row['error_type']=type(error).__name__
                                if not isinstance(error, APIError): stopped='unexpected_error'
                                elif getattr(error, 'status_code', None) in (400, 401, 403, 404): stopped='model_or_parameter_access_error'
                            row['usage']=usage.events
                        if any((e.get('details',{}).get('reasoning_tokens') or 0)>0 for e in usage.events): stopped='thinking_mode_mismatch'
                        row['budget']=budget.snapshot()
                        write(directory, name+'-result.json', row)
                        results.append(row)
                        print(json.dumps(dict(case_id=row['case_id'],model=model,repeat=repeat,error=row.get('error_type'),budget=row['budget'])),flush=True)
                        if stopped: raise EvaluationStopped(stopped)
    except EvaluationStopped as error:
        stopped=str(error)
    finally:
        client.close()
        write(directory, 'results.json', results)
        write(directory, 'closed.json', dict(status='closed',at=datetime.now(timezone.utc).isoformat(), budget=budget.snapshot(),
            completed=len(results),stopped=stopped,pending=len(list(directory.glob('*-started.json')))-len(results),
            unused_allowance_reusable=False,retries=0,gold100_score=False))


def finish(directory):
    from backend.app.manufacturing.answer_context import manufacturing_answer_context
    from backend.app.knowledge.output_contract import live_generation_schema, validate_output
    from backend.app.knowledge.answer_service import AnswerService
    from backend.app.knowledge.answer_validation import AnswerValidator
    plan=read(directory/'plan.json')
    records,citations=load_source(ROOT/plan['source_directory'])
    cases={c['id']:c for c in map(json.loads,(ROOT/'evals/manufacturing_gold_100_v4/cases.jsonl').read_text(encoding='utf-8').splitlines())}
    observations=[]
    for row in read(directory/'results.json'):
        observation={k:row[k] for k in ('case_id','model','repeat')}
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
                except (ValueError,TypeError) as error: observation['schema_error']=type(error).__name__
        observations.append(observation)
    assert all(digest(ROOT/p)==h for p,h in plan['protected'].items())
    write(directory, 'observations.json', observations)
    print(json.dumps(dict(observations=len(observations),local_valid=sum(o['valid'] for o in observations),protected_inputs_unchanged=True,model_calls=0)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('freeze','run','finish'))
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--credentials',type=Path)
    args=parser.parse_args()
    directory=args.run_dir.resolve()
    directory.relative_to(ROOT/'tmp')
    if args.mode=='freeze': freeze(directory)
    elif args.mode=='run': run(directory,args.credentials.resolve())
    else: finish(directory)
