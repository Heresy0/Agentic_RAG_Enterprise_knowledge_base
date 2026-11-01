"""Budgeted real-model acceptance through the authenticated ASGI manufacturing HTTP routes.

An in-process HTTP client propagates the physical-call budget into every model
boundary. It uses the same application, database, auth, MES and retrieval as the
deployed image; this is not an external gateway/load test. No paid request retries.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import traceback
from time import monotonic, perf_counter
from urllib.parse import urlsplit

import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app.quality.eval_budget import EvaluationBudget, EvaluationStopped, evaluation_budget
from backend.app.manufacturing.contracts import WorkOrderFixtures
from backend.app.evaluation.manufacturing_metrics import score_frozen_workorder_case
from backend.app.evaluation.trace import capture_trace
from backend.app.model_usage import summarize_usage
from backend.app.quality.verification import runtime_config

_running_batch=None


class AcceptanceBudget(EvaluationBudget):
    request_deadline=None
    def before_call(self):
        if self.request_deadline is not None and monotonic()>=self.request_deadline:self.limited_by="request_time"
        super().before_call()


def write_new(path,value):
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)


def percentile(values,p):
    return sorted(values)[math.ceil(p*len(values))-1] if values else None


def token_from_file(path):
    data=json.loads(path.read_text(encoding='utf-8-sig'));token=data['知识管理员']['access_token']
    claims=json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'=='))
    if claims['exp']-datetime.now(timezone.utc).timestamp()<200:
        url=os.environ.get('OIDC_JWKS_URL','http://127.0.0.1:8080/realms/enterprise-knowledge/protocol/openid-connect/certs').replace('/certs','/token')
        issuer=os.environ.get('OIDC_ISSUER',claims['iss']).rstrip('/')
        if claims['iss'].rstrip('/')!=issuer:raise ValueError('验收身份签发方与配置不符。')
        response=requests.post(url,headers={'Host':urlsplit(issuer).netloc},data=dict(grant_type='refresh_token',client_id='enterprise-knowledge-cli',
            refresh_token=data['知识管理员']['refresh_token']),timeout=20)
        if response.status_code!=200:raise ValueError('验收身份刷新失败。')
        data['知识管理员'].update(response.json());token=data['知识管理员']['access_token']
        temporary=path.with_suffix('.pending');temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');temporary.replace(path)
    return token


def select_cases(phase):
    splits=['development'] if phase=='development' else ['development','regression']
    return [json.loads(line) for split in splits for line in (ROOT/'evals/manufacturing_workorder_v1'/split/'single_turn.jsonl').read_text(encoding='utf-8').splitlines() if line]


def workload(phase,prior=None):
    cases=select_cases(phase);expected=16 if phase=='development' else 40
    if len(cases)!=expected:raise ValueError('冻结题数不符。')
    limits=dict(max_cases=expected,max_model_calls=128 if expected==16 else 320,
        max_tokens=800000 if expected==16 else 2000000,max_seconds=1440 if expected==16 else 3600,reservation_tokens=32000)
    provenance=None
    if prior is not None:
        if phase!='development':raise ValueError('正式40题不允许拼接或补跑。')
        prior=prior.resolve();prior.relative_to(ROOT/'tmp')
        report=json.loads((prior/'partial-report.json').read_text(encoding='utf-8'))
        manifest=json.loads((prior/'manifest.json').read_text(encoding='utf-8'))
        responses=[json.loads(line) for line in (prior/'responses.jsonl').read_text(encoding='utf-8').splitlines()]
        if (report['manifest_sha256']!=hashlib.sha256((prior/'manifest.json').read_bytes()).hexdigest() or
                report['responses_sha256']!=hashlib.sha256((prior/'responses.jsonl').read_bytes()).hexdigest() or
                manifest['case_ids']!=[row['id'] for row in cases] or not report['source_ledger_reconciled'] or
                report['executed_case_ids']!=[row['case_id'] for row in responses]):
            raise ValueError('部分批次的原始范围或响应绑定不符。')
        ledger=json.loads((prior/'partial-usage-ledger.json').read_text(encoding='utf-8'))
        charged=sum(row['total_tokens'] if row['total_tokens'] is not None else limits['reservation_tokens'] for row in ledger)
        consumed=report['budget_consumed']
        if consumed['calls']!=len(ledger) or consumed['charged_tokens']!=charged:raise ValueError('部分批次账本不一致。')
        done={row['case_id'] for row in responses}
        cases=[row for row in cases if row['id'] not in done]
        if [row['id'] for row in cases]!=report['unexecuted_case_ids']:raise ValueError('未执行清单不符。')
        limits.update(max_cases=len(cases),max_model_calls=limits['max_model_calls']-consumed['calls'],
            max_tokens=limits['max_tokens']-consumed['charged_tokens'],max_seconds=limits['max_seconds']-consumed['reserved_elapsed_seconds'])
        if any(limits[key]<=0 for key in ('max_cases','max_model_calls','max_tokens','max_seconds')):raise ValueError('原预算已耗尽，不建立新付费范围。')
        provenance=dict(prior_manifest_sha256=report['manifest_sha256'],prior_responses_sha256=report['responses_sha256'],
            report_sha256=hashlib.sha256((prior/'partial-report.json').read_bytes()).hexdigest(),
            purpose='Only previously unexecuted questions; no retry or combined independent accuracy.')
    return cases,limits,provenance


def preflight_environment(app,token,fixtures):
    from sqlalchemy import select,func
    from backend.app.db.models import User,KnowledgeDocument,DocumentChunk
    from backend.app.security.principal import Principal
    from backend.app.security.authorization import AuthorizationService
    from backend.app.manufacturing.evaluation_sources import registry_for
    from backend.app.manufacturing.contracts import canonical_digest
    from backend.app.manufacturing.metadata import current_review,validate_anchors
    from backend.app.documents.publication import is_published
    # The same token is also verified by the authenticated HTTP preflight.
    subject=json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'=='))['sub']
    manifest=json.loads((ROOT/'sample_docs/manufacturing_workorder_v1/manifest.json').read_text(encoding='utf-8'))
    expected={Path(row['file']).name:row for row in manifest['documents']}
    with app.state.database_session_factory() as session:
        user=session.scalar(select(User).where(User.external_subject==subject,User.tenant_id==fixtures.tenant_id,User.status=='active'))
        if user is None:raise ValueError('验收身份没有独立租户映射。')
        principal=Principal(user.id,user.tenant_id,user.external_subject)
        documents=list(session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id==fixtures.tenant_id)))
        if len(documents)!=13 or {row.file_name for row in documents}!=set(expected):raise ValueError('独立语料清单不完整或有额外资料。')
        inventory=[]
        for doc in documents:
            AuthorizationService(session).require_permission(principal=principal,knowledge_base_id=doc.knowledge_base_id,required_permission='admin')
            review=current_review(doc)
            if doc.status!='ready' or doc.content_hash!=expected[doc.file_name]['sha256'] or not is_published(doc) or review is None:
                raise ValueError('原件、索引、发布或制造复核预检失败。')
            validate_anchors(session,doc,review.data)
            inventory.append(dict(document_id=str(doc.id),file_name=doc.file_name,content_hash=doc.content_hash,version=doc.version,manufacturing_revision=review.revision))
        registry=registry_for(app.state.settings)
        binding=registry.check(session,principal)
        if (binding['fixture_version']!=fixtures.fixture_version or binding['fixture_sha256']!=canonical_digest(fixtures.model_dump(mode='json'))):
            raise ValueError('活跃源夹具与冻结输入不符。')
        sql_count=session.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.tenant_id==fixtures.tenant_id))
        vector_count=app.state.answer_service._retrieval_service._vector_store.count('tenant_id == "'+str(fixtures.tenant_id)+'"')
        if sql_count!=vector_count:raise ValueError('独立向量和 SQL 分块数量不符。')
        return dict(inventory=inventory,source_binding=binding,sql_chunks=sql_count,vector_records=vector_count)


def main(argv=None):
    global _running_batch
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['development','formal'],required=True)
    parser.add_argument('--tokens',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--allow-model-calls',action='store_true')
    parser.add_argument('--unexecuted-from',type=Path,help='Development only: reconciled original partial batch; excludes all executed questions and deducts its budget')
    args=parser.parse_args(argv)
    if not args.allow_model_calls:raise ValueError('须显式允许本批真实模型调用。')
    output=args.output_dir.resolve();output.relative_to(ROOT/'tmp')
    if output.exists():raise ValueError('已有批次，拒绝覆盖或自动补跑。')
    from scripts.check_manufacturing_workorder_profile import check
    preservation=check(ROOT)
    cases,limits,provenance=workload(args.phase,args.unexecuted_from)
    fixtures=WorkOrderFixtures.model_validate_json((ROOT/'sample_docs/manufacturing_workorder_v1/work_orders.json').read_bytes())
    source_records={row.work_order_id:row for row in fixtures.records}
    budget=AcceptanceBudget(limits)
    output.mkdir(parents=True);(output/'traces').mkdir()
    _running_batch=dict(output=output,budget=budget)
    write_new(output/'preservation.json',preservation)
    write_new(output/'manifest.json',dict(phase=args.phase,started_at=datetime.now(timezone.utc).isoformat(),limits=limits,
        case_ids=[row['id'] for row in cases],transport='authenticated_in_process_asgi_http',continuation=provenance,
        limitation='真实依赖和模型；非外部网关压测；语义复核单独绑定实际响应，助手复核非独立。',allow_model_calls=True))
    with (output/'questions.jsonl').open('x',encoding='utf-8') as stream:
        for row in cases:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    from fastapi.testclient import TestClient
    from backend.app.main import app
    records=[];usage=[];source_latencies=[]
    with TestClient(app) as client:
        settings=app.state.settings
        write_new(output/'runtime.json',runtime_config(settings))
        headers=dict(Authorization='Bearer '+token_from_file(args.tokens))
        context_reads=[]
        for _ in range(20):
            started=perf_counter();response=client.get('/api/manufacturing/work-orders/WO-017/context',headers=headers)
            elapsed=round((perf_counter()-started)*1000,3)
            context_reads.append(dict(status=response.status_code,elapsed_ms=elapsed,request_id=response.headers.get('x-request-id')))
            if response.status_code!=200:raise ValueError('合法工单读取预检失败，尚未开始真实模型调用。')
            if response.json()['payload_sha256']!=source_records['WO-017'].payload_sha256:raise ValueError('工单来源与冻结夹具不符。')
            source_latencies.append(elapsed)
        environment=preflight_environment(app,headers['Authorization'][7:],fixtures)
        write_new(output/'preflight.json',dict(status='passed',model_calls=0,context_reads=context_reads,
            source_p95_ms=percentile(source_latencies,.95),source_p95_passed=percentile(source_latencies,.95)<=500,
            fixture_version=fixtures.fixture_version,limits=limits,**environment))
        stopped=None
        for case in cases:
            if stopped:
                records.append(dict(case_id=case['id'],status='not_executed',error=stopped));continue
            try:budget.check()
            except EvaluationStopped as exc:
                stopped=type(exc).__name__;records.append(dict(case_id=case['id'],status='not_executed',error=stopped));continue
            header=dict(Authorization='Bearer '+token_from_file(args.tokens))
            binding=case['business_context_binding']
            payload=dict(question=case['question'],work_order_id=binding['work_order_id'],
                knowledge_base_id=None,expected_source_revision=binding['source_revision'])
            # Only these trusted request DTO fields go into QA. Reference answers,
            # facts, evidence labels and case IDs remain outside the ASGI request.
            def ask():
                with evaluation_budget(budget),capture_trace() as trace:
                    response=client.post('/api/manufacturing/qa',json=payload,headers=header)
                    return response,trace.to_dict()
            started=perf_counter();budget.request_deadline=monotonic()+180
            executor=ThreadPoolExecutor(max_workers=1)
            future=executor.submit(ask)
            try:
                response,trace=future.result(timeout=180)
            except FutureTimeout:
                budget.limited_by='request_time';stopped='request_timeout'
                row=dict(case_id=case['id'],status='timeout',elapsed_ms=180000,request=payload)
                with (output/'responses.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
                records.append(row)
                # Provider calls cannot be cancelled. Stop the batch and retain
                # their eventual trace/usage, never issue another paid request.
                try:
                    late,trace=future.result()
                    write_new(output/'traces'/(case['id']+'-late.json'),dict(trace=trace,status=late.status_code,response=late.json()))
                    usage.extend(trace.get('stages',{}).get('model_usage',[]))
                finally:executor.shutdown(wait=True)
                continue
            finally:
                if future.done():executor.shutdown(wait=True)
            elapsed=round((perf_counter()-started)*1000,3)
            body=response.json()
            row=dict(case_id=case['id'],status=response.status_code,request=payload,response=body,
                request_id=response.headers.get('x-request-id'),elapsed_ms=elapsed)
            # Persist the paid response before any follow-up observation/scoring.
            with (output/'responses.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            write_new(output/'traces'/(case['id']+'.json'),trace)
            usage.extend(trace.get('stages',{}).get('model_usage',[]))
            if response.status_code==200:
                snapshot=client.get('/api/qa/runs/'+body['run_id'],headers=header)
                if snapshot.status_code!=200:raise ValueError('成功回答缺少可访问快照。')
                observed=snapshot.json();write_new(output/'traces'/(case['id']+'-snapshot.json'),observed)
                context=(observed['snapshot'].get('context') or {}).get('items',[])
                row['metrics']=score_frozen_workorder_case(case,body,context,source_records[binding['work_order_id']])
                row['response_binding']=hashlib.sha256(json.dumps(dict(case=case,response=body,snapshot=observed),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            records.append(row)
            print(json.dumps(dict(case_id=case['id'],status=response.status_code,elapsed_ms=elapsed,
                proxy_pass=row.get('metrics',{}).get('automatic_proxy_pass'),budget=budget.snapshot()),ensure_ascii=True),flush=True)
            if budget.limited_by:stopped=budget.limited_by
    with (output/'records.jsonl').open('x',encoding='utf-8') as stream:
        for row in records:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    good=[row for row in records if row.get('metrics',{}).get('automatic_proxy_pass')]
    positive_ids={row['id'] for row in cases if row['answerable']}
    success_latencies=[row['elapsed_ms'] for row in good if row['case_id'] in positive_ids]
    report=dict(total=len(cases),executed=sum(row.get('status')!= 'not_executed' for row in records),automatic_proxy_passed=len(good),
        errors=sum(row.get('status')!=200 for row in records),budget=budget.snapshot(),usage=summarize_usage(usage),
        source_p95_ms=percentile(source_latencies,.95),answer_proxy_correct_count=len(success_latencies),
        answer_proxy_correct_p50_ms=percentile(success_latencies,.5),answer_proxy_correct_p95_ms=percentile(success_latencies,.95),
        answer_proxy_correct_max_ms=max(success_latencies) if success_latencies else None,
        semantic_review='pending',limitation='代理正确题延迟；待逐题语义复核后另算正确回答延迟。无补跑、无独立人工复核。')
    write_new(output/'usage.json',dict(usage=summarize_usage(usage),events=usage,budget=budget.snapshot()))
    write_new(output/'report.json',report)
    print(json.dumps(dict(total=report['total'],automatic_proxy_passed=report['automatic_proxy_passed'],errors=report['errors'],budget=budget.snapshot()),ensure_ascii=True))
    return 0 if not report['errors'] else 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        if _running_batch is not None:
            failure=dict(status='interrupted',error_type=type(exc).__name__,budget=_running_batch['budget'].snapshot(),
                frames=[dict(file=row.filename,function=row.name,line=row.lineno) for row in traceback.extract_tb(exc.__traceback__)],
                recorded_at=datetime.now(timezone.utc).isoformat())
            path=_running_batch['output']/'failure.json'
            if not path.exists():write_new(path,failure)
        print(json.dumps(dict(status='failed',error_type=type(exc).__name__)),file=sys.stderr)
        raise SystemExit(1) from None
