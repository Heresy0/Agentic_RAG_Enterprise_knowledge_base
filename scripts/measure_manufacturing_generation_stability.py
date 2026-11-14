"""Freeze reconstructed inputs, then measure repeated generation without retrieval.

This is a component experiment, never a replacement gold100 score or live-source
acceptance. Freeze/finish are offline; run is a new explicitly bounded paid batch.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CASES = (4, 6, 10, 12, 21, 26, 43, 51, 60, 64, 91, 95)
RERANK_CASES = (9, 26, 60)
LIMITS = dict(max_model_calls=45, max_tokens=1500000, max_seconds=5400, reservation_tokens=32000)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(directory, name, data):
    with (directory/name).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def frozen_context(record, question, citation_metadata):
    # This existing helper denies network/DNS for the entire offline process.
    from scripts.diagnose_manufacturing_answer_focus_regressions import frozen_context as reconstruct
    from backend.app.documents.policy_metadata import policy_label
    from backend.app.manufacturing.answer_context import attach_business_context
    context, active = reconstruct(record, question)
    blocks, items = [], []
    snapshot = next((e['response']['snapshot']['manufacturing'] for e in record['events']
                     if e['method']=='GET' and isinstance(e.get('response'), dict)
                     and e['response'].get('snapshot', {}).get('manufacturing')), None)
    for item in context.items:
        meta = next(v for v in record['final']['trace']['stages']['authorized_final'] if v['chunk_id']==item.chunk_id)
        citation = citation_metadata.get(meta['document_id'], {})
        binding = next((d for d in snapshot['applicability']['documents'] if d['document_id']==meta['document_id']), {}) if snapshot else {}
        item = replace(item, document_id=meta['document_id'], section_path=meta.get('section_path') or '未标注章节',
                       knowledge_base_name=citation.get('knowledge_base_name'), document_version=citation.get('document_version'),
                       business_revision=binding.get('business_revision'), document_role=binding.get('document_role'))
        policy = policy_label(item.policy)
        header = f'适用性：{policy}\n' if policy else ''
        if binding:
            header += (f"制造文件：{binding.get('document_code')}；业务版本：{item.business_revision}；"
                       f"技术版本：{item.document_version}；角色：{item.document_role}\n")
        kb = f'知识库：{item.knowledge_base_name}\n' if item.knowledge_base_name else ''
        blocks.append(f'[{item.citation_id}]\n{kb}文档：{item.document_name}\n章节：{item.section_path}\n{header}内容：{item.content}')
        items.append(item)
    text = '\n\n'.join(blocks)
    context = replace(context, text=text, items=items, total_characters=len(text))
    from backend.app.manufacturing.answer_context import manufacturing_answer_context
    with manufacturing_answer_context(active):
        context = attach_business_context(context, question=question) if active else context
    return context, active


def load_source(directory):
    records = {r['case_id']:r for r in read(directory/'evaluation-results.json')}
    citations = {c['document_id']:c for r in records.values() for c in (r.get('final') or {}).get('response', {}).get('citations', [])}
    return records, citations


def freeze(directory, source):
    records, citations = load_source(source)
    gold = {c['id']:c for c in map(json.loads, (ROOT/'evals/manufacturing_gold_100_v4/cases.jsonl').read_text(encoding='utf-8').splitlines())}
    from scripts.diagnose_manufacturing_answer_focus_regressions import manufacturing_answer_context
    from backend.app.knowledge.answer_service import AnswerService
    from backend.app.knowledge.output_contract import live_generation_schema
    from backend.app.knowledge.evidence_selection import render_for_rerank
    from langchain_core.documents import Document
    from langchain_openai import ChatOpenAI
    import httpx
    entries = []
    for number in CASES:
        key = f'MFG-GOLD-{number:03}'; record = records[key]
        context, active = frozen_context(record, gold[key]['question'], citations)
        captured = []
        def intercept(request):
            captured.append(json.loads(request.content))
            # No network. The SDK response deliberately stops at this fixture.
            return httpx.Response(200, json=dict(id='freeze', choices=[dict(index=0, finish_reason='stop',
                message=dict(role='assistant', content=json.dumps(dict(answerable=False,response_type='refuse',
                    claims=[],refusal_reason='freeze',coverage=[dict(requirement_id='Q1',aspect='freeze',
                        status='insufficient',claim_indices=[],missing_information='freeze')]))))],
                created=0, model='frozen', object='chat.completion'))
        with httpx.Client(transport=httpx.MockTransport(intercept)) as client, manufacturing_answer_context(active):
            model = ChatOpenAI(model='qwen3.7-flash', base_url='https://unused.invalid/v1', api_key='freeze-only',
                               temperature=0, reasoning_effort='none', timeout=120, max_retries=0,
                               http_client=client, http_socket_options=())
            model.with_structured_output(live_generation_schema(context), include_raw=True).invoke(
                AnswerService._generation_messages(None, gold[key]['question'], context))
        assert len(captured)==1
        entries.append(dict(case_id=key, kind='generation', payload=captured[0]))
    for number in RERANK_CASES:
        key=f'MFG-GOLD-{number:03}'; record=records[key]
        documents=[render_for_rerank(Document(page_content=d['content'], metadata=d))
                   for d in record['final']['trace']['stages']['policy_candidates']]
        entries.append(dict(case_id=key, kind='rerank_probe', documents=documents, query=record['final']['trace']['query']))
    for entry in entries:
        entry['request_sha256']=hashlib.sha256(json.dumps(entry,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    write(directory, 'inputs.json', entries)
    protected = {str(p.relative_to(ROOT)).replace('\\','/'):digest(p) for p in (ROOT/'backend/app').rglob('*.py')}
    for p in (ROOT/'evals/manufacturing_gold_100_v4').glob('*'):
        if p.is_file():protected[p.relative_to(ROOT).as_posix()]=digest(p)
    for p in source.glob('MFG-GOLD-*-result.json'):protected[p.relative_to(ROOT).as_posix()]=digest(p)
    write(directory, 'plan.json', dict(source_directory=source.relative_to(ROOT).as_posix(),
        source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
        generation_cases=CASES, rerank_cases=RERANK_CASES, repeats=3, limits=LIMITS,
        input_sha256=digest(directory/'inputs.json'), protected=protected,
        basis='Six r12 successes and six failures, chosen before repeated outputs. Fixed r12 reconstructed inputs.',
        labels_sent=False, retries=0, gold100_score=False, changing_prompt_schema_retrieval_validation=False,
        limitation='Reconstructed document headers; unmatched-child header metadata was not saved. Not exact historical full requests or fresh source/permission validation.'))
    print(json.dumps(dict(frozen_generation_cases=len(CASES), rerank_probe_cases=len(RERANK_CASES), attempts=45, network_calls=0)))


def run(directory, credentials):
    from openai import OpenAI
    from backend.app.model_usage import ModelCall, usage_context
    from backend.app.security.retrieval_scope import RetrievalScope
    from backend.app.quality.eval_budget import EvaluationBudget, evaluation_budget, EvaluationStopped
    from backend.app.knowledge.reranker import QwenReranker, RerankError
    plan=read(directory/'plan.json'); assert digest(directory/'inputs.json')==plan['input_sha256'] and plan['limits']==LIMITS
    assert all(digest(ROOT/p)==h for p,h in plan['protected'].items())
    config=dict(line.split('=',1) for line in credentials.read_text(encoding='utf-8').splitlines() if '=' in line and not line.startswith('#'))
    assert config['DASHSCOPE_MODEL']=='qwen3.7-flash' and config['DASHSCOPE_RERANK_MODEL']=='qwen3-rerank'
    assert float(config['RERANK_TIMEOUT_SECONDS'])==5
    write(directory, 'started.json', dict(at=datetime.now(timezone.utc).isoformat(), authorization='用户批准实施运行诊断与固定输入稳定性对照', limits=LIMITS))
    budget=EvaluationBudget(plan['limits']); records=[]; stopped=None
    client=OpenAI(api_key=config['DASHSCOPE_API_KEY'], base_url=config['DASHSCOPE_BASE_URL'], timeout=120, max_retries=0)
    reranker=QwenReranker(endpoint=config['DASHSCOPE_RERANK_URL'], api_key=config['DASHSCOPE_API_KEY'],
                         model=config['DASHSCOPE_RERANK_MODEL'], timeout_seconds=5)
    scope=RetrievalScope(UUID('00000000-0000-0000-0000-000000000001'), UUID('00000000-0000-0000-0000-000000000002'))
    try:
        with evaluation_budget(budget):
            for repeat in range(1, plan['repeats']+1):
                for entry in read(directory/'inputs.json'):
                    budget.check(); assert digest(directory/'inputs.json')==plan['input_sha256']
                    name=f"{entry['kind']}-{entry['case_id']}-{repeat}"
                    row=dict(case_id=entry['case_id'], kind=entry['kind'], repeat=repeat,
                             request_sha256=entry['request_sha256'],input_sha256=plan['input_sha256'])
                    write(directory, name+'-started.json', row)
                    with usage_context(scope=scope, request_id=name) as usage:
                        try:
                            if entry['kind']=='generation':
                                with ModelCall('answer_generation', config['DASHSCOPE_MODEL']) as call:
                                    response=client.chat.completions.create(**entry['payload'])
                                    call.observe(response.usage.model_dump() if response.usage else None, response._request_id)
                                    choice=response.choices[0]
                                    row.update(content=choice.message.content, refusal=choice.message.refusal, finish_reason=choice.finish_reason)
                                try:row['parsed']=json.loads(row['content'])
                                except (ValueError, TypeError):row['parse_error']='invalid_json'
                            else:
                                result=reranker.rerank(query=entry['query'], documents=entry['documents'], top_n=len(entry['documents']))
                                row['ranking']=[dict(index=r.index,score=r.relevance_score) for r in result]
                        except EvaluationStopped:
                            raise
                        except Exception as error:
                            # A failed planned attempt is retained, never retried.
                            # Programming errors stop the batch after saving its row.
                            row['error_type']=type(error).__name__
                            from openai import APIError
                            if not isinstance(error,(APIError,RerankError)):
                                stopped='unexpected_error'
                        row['usage']=usage.events
                    row['budget']=budget.snapshot();write(directory, name+'-result.json', row); records.append(row)
                    print(json.dumps(dict(case_id=row['case_id'],repeat=repeat,kind=row['kind'],error=row.get('error_type'),budget=row['budget'])),flush=True)
                    if stopped:raise EvaluationStopped(stopped)
    except EvaluationStopped as error:
        stopped=str(error)
    finally:
        client.close()
        write(directory,'results.json',records)
        write(directory,'closed.json',dict(status='closed',at=datetime.now(timezone.utc).isoformat(), budget=budget.snapshot(),
            completed=len(records),stopped=stopped,pending=len(list(directory.glob('*-started.json')))-len(records),
            unused_allowance_reusable=False, retries=0, gold100_score=False))


def finish(directory):
    plan=read(directory/'plan.json');records,citations=load_source(ROOT/plan['source_directory'])
    from scripts.diagnose_manufacturing_answer_focus_regressions import manufacturing_answer_context
    from backend.app.knowledge.output_contract import live_generation_schema, validate_output
    from backend.app.knowledge.answer_service import AnswerService
    from backend.app.knowledge.answer_validation import AnswerValidator
    cases={c['id']:c for c in map(json.loads,(ROOT/'evals/manufacturing_gold_100_v4/cases.jsonl').read_text(encoding='utf-8').splitlines())}
    observations=[]
    for row in read(directory/'results.json'):
        if row['kind']!='generation':continue
        observation=dict(case_id=row['case_id'],repeat=row['repeat'],error=row.get('error_type'),answer=None,valid=False)
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
    print(json.dumps(dict(generation_observations=len(observations),local_valid=sum(o['valid'] for o in observations),protected_inputs_unchanged=True,model_calls=0)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('freeze','run','finish'));parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--source-dir',type=Path);parser.add_argument('--credentials',type=Path)
    args=parser.parse_args();directory=args.run_dir.resolve();directory.relative_to(ROOT/'tmp')
    if args.mode=='freeze':freeze(directory,args.source_dir.resolve())
    elif args.mode=='run':run(directory,args.credentials.resolve())
    else:finish(directory)


if __name__=='__main__':main()
