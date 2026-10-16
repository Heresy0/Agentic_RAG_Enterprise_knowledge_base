"""Optional Ragas judge over frozen answers/context; never executes the RAG service."""
import argparse
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
ACTIVE=ContextVar('ragas_saved_sample', default={})


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def response_text(response):
    text=response['answer']
    if response['answerable'] is False and response.get('refusal_reason'):
        text+='\n拒答原因：'+response['refusal_reason']
    return text


def apply_reference_overlay(samples, dataset, rules_path, overlay_path):
    """Change only gold wording; bind annotations to the exact historical criteria."""
    overlay=json.loads(overlay_path.read_text(encoding='utf-8'))
    rules=json.loads(rules_path.read_text(encoding='utf-8'))
    cases=[json.loads(l) for l in dataset.read_text(encoding='utf-8-sig').splitlines() if l.strip()]
    if overlay['dataset_sha256']!=digest(dataset) or overlay['scoring_rules_sha256']!=digest(rules_path):
        raise ValueError('Reference overlay source hash differs')
    rows=overlay['cases'];by_id={r['id']:r for r in rows}
    if len(rows)!=len(samples) or len(by_id)!=len(rows) or set(by_id)!={s['id'] for s in samples}:
        raise ValueError('Reference overlay case sets differ')
    gold={c['id']:c for c in cases}
    result=[]
    for sample in samples:
        row=by_id[sample['id']];case=gold[sample['id']]
        effective={**case,**rules['cases'].get(sample['id'],{})}
        canonical=json.dumps(effective,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
        if (row['question_sha256']!=hashlib.sha256(case['question'].encode('utf-8')).hexdigest()
            or row['original_reference_sha256']!=hashlib.sha256(case['reference_answer'].encode('utf-8')).hexdigest()
            or row['original_reference']!=case['reference_answer']
            or row['original_criteria_sha256']!=hashlib.sha256(canonical).hexdigest()
            or row['answerable']!=case['answerable']
            or any(row[key]!=(effective.get(key) or []) for key in
                   ('required_facts','optional_facts','semantic_checks','forbidden_claims'))):
            raise ValueError('Reference overlay changed historical facts or criteria')
        if not isinstance(row['required_reference'],str) or not row['required_reference'].strip():
            raise ValueError('Empty required reference')
        if not isinstance(row['optional_information'],list) or any(not isinstance(x,str) or not x.strip() for x in row['optional_information']):
            raise ValueError('Invalid optional reference information')
        result.append(dict(sample,reference=row['required_reference'],original_reference=sample['reference'],
            optional_reference_information=row['optional_information'],reference_version=overlay['version']))
    return result,overlay


def prepare_samples(dataset, report_path, traces_path, expected_cases=100):
    cases=[json.loads(line) for line in dataset.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if expected_cases is None:expected_cases=len(cases)
    report=json.loads(report_path.read_text(encoding='utf-8'))
    traces=[json.loads(line) for line in traces_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    records=report['layers']['answer']['records']
    if report['dataset_sha256']!=digest(dataset):raise ValueError('Dataset hash differs from frozen report')
    indexes=[]
    for values in (cases,traces,records):
        index={row['id']:row for row in values}
        if len(values)!=expected_cases or len(index)!=expected_cases:raise ValueError('Duplicate or missing cases')
        indexes.append(index)
    cs,ts,rs=indexes
    if set(cs)!=set(ts) or set(cs)!=set(rs):raise ValueError('Case sets differ')
    samples=[]
    for i,c in cs.items():
        t=ts[i];r=rs[i];response=t.get('response')
        if t.get('question_hash')!=hashlib.sha256(c['question'].encode()).hexdigest():raise ValueError('Question changed')
        if not response or type(response.get('answerable')) is not bool:raise ValueError('Final response missing')
        if r.get('response')!=response:raise ValueError('Report/trace final answers differ')
        if r['status'] not in ('passed','failed'):raise ValueError('Source case not evaluated')
        context=t.get('stages',{}).get('context')
        if not context or any(row.get('scope_violation') for row in context):raise ValueError('Missing/invalid actual context')
        samples.append(dict(id=i,user_input=c['question'],reference=c['reference_answer'],
            response=response_text(response),answerable=response['answerable'],expected_answerable=c['answerable'],
            original_status=r['status'],original_metrics=r.get('metrics'),
            retrieved_contexts=[f"[{row['citation_id']}]\n文档：{row['document_name']}\n内容：{row['content']}" for row in context]))
    return samples,report


def score_value(value):
    value=float(value)
    if not math.isfinite(value):return None
    if not 0<=value<=1:raise ValueError('Metric outside0..1')
    return value


def screen(row, thresholds):
    if any(row.get(metric,{}).get('status')!='ok' for metric in thresholds):return None
    return all(row[metric]['value']>=threshold for metric,threshold in thresholds.items())


def write_summary(out,samples,rows,protocol,thresholds):
    by_id={row['id']:row for row in rows}
    metrics={}
    for metric in thresholds:
        values=[r[metric]['value'] for r in rows if r.get(metric,{}).get('status')=='ok']
        metrics[metric]=dict(scored=len(values),mean=statistics.mean(values) if values else None,
            undefined=sum(r.get(metric,{}).get('status')=='undefined' for r in rows),
            errors=sum(r.get(metric,{}).get('status')=='error' for r in rows),
            at_or_above_threshold=sum(v>=thresholds[metric] for v in values),threshold=thresholds[metric])
    compared=[]
    for sample in samples:
        r=by_id.get(sample['id'])
        if not r:continue
        judged=screen(r,thresholds)
        compared.append(dict(id=sample['id'],expected_answerable=sample['expected_answerable'],
            answerable=sample['answerable'],original_status=sample['original_status'],
            judge_screen_pass=judged,disagreement=judged is not None and judged!=(sample['original_status']=='passed'),
            **{key:r.get(key) for key in thresholds}))
    summary=dict(protocol=protocol,completed_cases=len(rows),expected_cases=len(samples),metrics=metrics,
        original_passed=sum(s['original_status']=='passed' for s in samples),
        judge_screen_scored=sum(r['judge_screen_pass'] is not None for r in compared),
        judge_screen_passed=sum(r['judge_screen_pass'] is True for r in compared),
        disagreements=[r['id'] for r in compared if r['disagreement']],records=compared)
    summary['evaluation_status']=('partial' if len(rows)!=len(samples) else
        'complete_with_metric_errors' if any(v['errors'] for v in metrics.values()) else 'complete')
    events=[json.loads(line) for line in (out/'judge-calls.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()] if (out/'judge-calls.jsonl').exists() else []
    attempts=[json.loads(line) for line in (out/'judge-attempts.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()] if (out/'judge-attempts.jsonl').exists() else []
    by_attempt={e['attempt_id']:e for e in events}
    summary['judge_usage']=dict(calls=len(attempts),reported_total_tokens=sum(e.get('total_tokens') or 0 for e in events),
        unknown_usage_calls=sum(by_attempt.get(a['attempt_id'],{}).get('total_tokens') is None for a in attempts),
        http_errors=sum(e.get('status_code',0)>=400 for e in events),no_response_calls=len(attempts)-len(events))
    (out/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


async def evaluate(args,samples,protocol,thresholds):
    # Ragas telemetry is disabled before importing the optional package.
    os.environ['RAGAS_DO_NOT_TRACK']='true'
    os.environ['LANGCHAIN_TRACING_V2']='false'
    os.environ['LANGSMITH_TRACING']='false'
    from importlib.metadata import version
    import httpx
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import FactualCorrectness, Faithfulness
    from dotenv import dotenv_values
    config={**dotenv_values(ROOT/'.env'),**os.environ}
    model=config.get('DASHSCOPE_MODEL','qwen3.7-flash')
    base_url=config.get('DASHSCOPE_BASE_URL','')
    key=config.get('DASHSCOPE_API_KEY','')
    if not base_url or not key:raise ValueError('Judge model configuration missing')
    from urllib.parse import urlsplit
    url=urlsplit(base_url)
    if url.scheme!='https' or url.hostname!='ws-u4iicqspr49vavjx.cn-beijing.maas.aliyuncs.com':raise ValueError('Unexpected judge destination')
    if model!='qwen3.7-flash':raise ValueError('Unexpected judge model')
    protocol.update(ragas_version=version('ragas'),judge_model=model,judge_base_url=url.scheme+'://'+url.netloc+url.path,
        sdk_retries=0,concurrency=args.concurrency,max_physical_calls=args.max_calls,
        instructor_max_attempts=2,temperature=0,top_p=.1,max_output_tokens=4096,reasoning_effort='none',
        dependency_versions={name:version(name) for name in ('ragas','instructor','openai','httpx','pydantic','langchain-community')},
        same_model_family_as_generator=True,ragas_telemetry=False)
    p=args.output_dir/'protocol.json'
    if p.exists():
        if json.loads(p.read_text(encoding='utf-8'))!=protocol:raise ValueError('Resume protocol differs')
    else:p.write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    results_path=args.output_dir/'scores.jsonl'
    rows=[json.loads(l) for l in results_path.read_text(encoding='utf-8').splitlines() if l.strip()] if results_path.exists() else []
    done={row['id'] for row in rows}
    if len(done)!=len(rows):raise ValueError('Duplicate saved scores')
    call_file=args.output_dir/'judge-calls.jsonl'
    attempt_file=args.output_dir/'judge-attempts.jsonl'
    call_count=sum(bool(line.strip()) for line in attempt_file.read_text(encoding='utf-8').splitlines()) if attempt_file.exists() else 0
    remaining=[s for s in samples if s['id'] not in done]
    if args.stop_after is not None:remaining=remaining[:args.stop_after]

    async def request_hook(request):
        nonlocal call_count
        if request.url.host!=url.hostname:raise ValueError('Unexpected HTTP destination')
        if call_count>=args.max_calls:raise RuntimeError('Physical judge call budget exhausted')
        call_count+=1
        request.extensions['evaluation_started']=time.perf_counter()
        request.extensions['evaluation_case']=dict(ACTIVE.get())
        request.extensions['evaluation_attempt']=str(uuid4())
        with attempt_file.open('a',encoding='utf-8') as f:
            f.write(json.dumps(dict(attempt_id=request.extensions['evaluation_attempt'],**ACTIVE.get(),started_at=datetime.now(timezone.utc).isoformat()),ensure_ascii=False)+'\n')

    async def response_hook(response):
        await response.aread()
        try:data=response.json()
        except Exception:data={}
        usage=data.get('usage') or {}
        choices=data.get('choices') or []
        row=dict(response.request.extensions.get('evaluation_case') or {},attempt_id=response.request.extensions['evaluation_attempt'],status_code=response.status_code,
            duration_ms=round((time.perf_counter()-response.request.extensions['evaluation_started'])*1000,2),
            input_tokens=usage.get('prompt_tokens'),output_tokens=usage.get('completion_tokens'),total_tokens=usage.get('total_tokens'),
            output=(choices[0].get('message') or {}).get('content') if choices and response.status_code<400 else None)
        with call_file.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')

    async with httpx.AsyncClient(event_hooks={'request':[request_hook],'response':[response_hook]},timeout=90) as transport:
        client=AsyncOpenAI(api_key=key,base_url=base_url,http_client=transport,max_retries=0)
        llm=llm_factory(model,client=client,temperature=0,max_tokens=4096,max_retries=2,reasoning_effort='none')
        metrics=dict(factual_correctness=FactualCorrectness(llm=llm,mode='f1',atomicity='high',coverage='high'),
                     faithfulness=Faithfulness(llm=llm))
        lock=asyncio.Lock();semaphore=asyncio.Semaphore(args.concurrency)

        async def one(sample):
            async with semaphore:
                row=dict(id=sample['id'])
                for name,metric in metrics.items():
                    token=ACTIVE.set(dict(id=sample['id'],metric=name))
                    started=time.perf_counter()
                    try:
                        kwargs=dict(response=sample['response'],reference=sample['reference']) if name=='factual_correctness' else dict(user_input=sample['user_input'],response=sample['response'],retrieved_contexts=sample['retrieved_contexts'])
                        result=await metric.ascore(**kwargs)
                        value=score_value(result.value)
                        row[name]=dict(status='ok' if value is not None else 'undefined',value=value,
                            reason=getattr(result,'reason',None))
                    except Exception as exc:
                        row[name]=dict(status='error',value=None,error_type=type(exc).__name__)
                    finally:ACTIVE.reset(token)
                    row[name]['duration_ms']=round((time.perf_counter()-started)*1000,2)
                async with lock:
                    rows.append(row)
                    with results_path.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
                    write_summary(args.output_dir,samples,rows,protocol,thresholds)
                    print(json.dumps(dict(completed=len(rows),id=row['id'],**{k:row[k] for k in metrics}),ensure_ascii=False),flush=True)

        await asyncio.gather(*(one(sample) for sample in remaining))
    return rows


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,default=ROOT/'evals/current/groups/main/single_turn.jsonl')
    p.add_argument('--source-report',type=Path,required=True)
    p.add_argument('--traces',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--reference-overlay',type=Path,help='Optional self-contained gold wording with unchanged r9 criteria')
    p.add_argument('--allow-model-calls',action='store_true')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--stop-after',type=int)
    p.add_argument('--concurrency',type=int,default=3,choices=range(1,5))
    p.add_argument('--max-calls',type=int,default=800)
    args=p.parse_args(argv)
    if not args.dry_run and not args.allow_model_calls:p.error('Explicit --allow-model-calls required')
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.resume:p.error('Use a new output directory or explicit --resume')
    samples,report=prepare_samples(args.dataset,args.source_report,args.traces,expected_cases=None)
    overlay=None
    if args.reference_overlay:
        rules_path=args.dataset.parent/'scoring_rules.json'
        if digest(rules_path)!=report.get('scoring_rules_sha256'):
            raise ValueError('Scoring rules differ from frozen report')
        samples,overlay=apply_reference_overlay(samples,args.dataset,rules_path,args.reference_overlay)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    thresholds=dict(factual_correctness=.8,faithfulness=.9)
    protocol=dict(dataset_sha256=digest(args.dataset),source_report_sha256=digest(args.source_report),
        source_traces_sha256=digest(args.traces),scorer_version=report['scorer_version'],
        scoring_rules_sha256=report.get('scoring_rules_sha256'),samples=len(samples),
        sample_ids=[s['id'] for s in samples],business_rag_calls=0,
        thresholds=thresholds,threshold_purpose='Diagnostic screen, not calibrated semantic accuracy or replacement of original scores.',
        adapter_sha256=digest(Path(__file__)),requirements_sha256=digest(ROOT/'requirements-evaluation.txt'),
        factual_correctness=dict(mode='f1',atomicity='high',coverage='high'),
        refusal_policy='Final answer plus saved user-visible refusal_reason; never draft claims.',
        context_policy='Only actual saved context text with original citation ID and document name; no additional sources.',
        timestamp_policy='Original question/date and references retained, not current-date regeneration.')
    if overlay:
        protocol.update(reference_overlay_sha256=digest(args.reference_overlay),reference_version=overlay['version'],
            reference_policy=overlay['policy'],
            timestamp_policy='Original question/date retained; self-contained reference wording preserves original dates and factual status.')
    (args.output_dir/'samples.jsonl').write_text(''.join(json.dumps(s,ensure_ascii=False)+'\n' for s in samples),encoding='utf-8')
    if args.dry_run:
        (args.output_dir/'dry-run.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(prepared=len(samples),original_passed=sum(s['original_status']=='passed' for s in samples),business_rag_calls=0),ensure_ascii=False))
        return 0
    rows=asyncio.run(evaluate(args,samples,protocol,thresholds))
    if digest(args.dataset)!=protocol['dataset_sha256'] or digest(args.source_report)!=protocol['source_report_sha256'] or digest(args.traces)!=protocol['source_traces_sha256']:raise ValueError('Frozen source changed during evaluation')
    if args.reference_overlay and digest(args.reference_overlay)!=protocol['reference_overlay_sha256']:
        raise ValueError('Reference overlay changed during evaluation')
    if len(rows)!=len(samples):return 2
    return 1 if any(r[m]['status']=='error' for r in rows for m in thresholds) else 0


if __name__=='__main__':raise SystemExit(main())
