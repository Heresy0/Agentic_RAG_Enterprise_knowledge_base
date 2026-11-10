"""Frozen v2-scored 16-question ERPNext batch; no retries or continuation mode.

Run preparation with a hard zero budget, then execute exactly once. The existing
application owns all model calls, permissions, source reads, generation/repair
and independent usage accounting. Gold labels never enter the QA request.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from time import monotonic, perf_counter, sleep
from urllib.parse import urlencode, urlsplit

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
from backend.app.quality.eval_budget import EvaluationBudget, EvaluationStopped, evaluation_budget
from backend.app.evaluation.trace import capture_trace
from backend.app.evaluation.manufacturing_execution_metrics import SCORER_VERSION

if SCORER_VERSION != 'manufacturing-execution-v2':
    raise ValueError('本入口固定使用执行评分v2。')

LIMITS = dict(max_cases=16, max_model_calls=128, max_tokens=800000, max_seconds=1440, reservation_tokens=32000)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load_suite(path):
    suite = read(path)
    cases = suite['cases']
    if (suite['schema_version'] != 'erpnext-development-v1' or suite['limits'] != LIMITS or len(cases) != 16
            or len({row['id'] for row in cases}) != 16 or sum(bool(row.get('critical_id')) for row in cases) != 6):
        raise ValueError('ERPNext题集、关键子集或授权预算不符。')
    return suite


class BatchBudget(EvaluationBudget):
    request_deadline = None

    def before_call(self):
        self.check()
        try:
            self.guard()
        except Exception:
            self.limited_by = 'source_or_authorization'
            raise EvaluationStopped(self.limited_by) from None
        self.check()
        # The longest unchanged provider timeout is 120 s, retries are zero.
        # Do not start another call near the batch deadline.
        if self.limits['max_seconds'] - (monotonic() - self.started) < 125:
            self.limited_by = 'time_reserve'
        elif self.request_deadline is not None and monotonic() >= self.request_deadline:
            self.limited_by = 'request_time'
        elif self.calls >= self.limits['max_model_calls']:
            self.limited_by = 'calls'
        elif self.charged_tokens + self.limits['reservation_tokens'] > self.limits['max_tokens']:
            self.limited_by = 'tokens'
        self.check()
        self.calls += 1


def token(path, role, app):
    import requests
    data = read(path)
    current = data[role]
    claims = json.loads(base64.urlsafe_b64decode(current['access_token'].split('.')[1] + '=='))
    if claims['iss'].rstrip('/') != app.state.settings.oidc_issuer.rstrip('/'):
        raise ValueError('签发方与当前环境不符。')
    if claims['exp'] - datetime.now(timezone.utc).timestamp() < 200:
        response = requests.post(os.environ['OIDC_JWKS_URL'].replace('/certs', '/token'),
            headers={'Host': urlsplit(claims['iss']).netloc}, data=dict(grant_type='refresh_token',
                client_id='enterprise-knowledge-cli', refresh_token=current['refresh_token']), timeout=20)
        if response.status_code != 200:
            raise ValueError('验证身份刷新失败；不重试。')
        current.update(response.json())
        fresh = json.loads(base64.urlsafe_b64decode(current['access_token'].split('.')[1] + '=='))
        if fresh['sub'] != claims['sub']:
            raise ValueError('刷新身份不一致。')
        pending = path.with_suffix('.pending')
        pending.write_text(json.dumps(data), encoding='utf-8'); pending.replace(path)
        claims = fresh
    delay = max(claims.get('iat', 0), claims.get('nbf', 0)) - datetime.now(timezone.utc).timestamp() + .2
    if delay > 0:
        if delay >= 5:
            raise ValueError('令牌生效时间偏移异常。')
        sleep(delay)
    app.state.oidc_token_verifier.verify(current['access_token'])
    return current['access_token'], claims['sub']


def principal_for(session, subject):
    from sqlalchemy import select
    from backend.app.db.models import User
    from backend.app.security.principal import Principal
    user = session.scalar(select(User).where(User.external_subject == subject, User.status == 'active'))
    if user is None:
        raise ValueError('验证身份没有活跃用户映射。')
    return Principal(user.id, user.tenant_id, user.external_subject)


def inventory(session, principal, corpus_manifest):
    from sqlalchemy import select
    from backend.app.db.models import KnowledgeDocument, DocumentChunk
    from backend.app.security.authorization import AuthorizationService
    from backend.app.documents.publication import is_published
    from backend.app.manufacturing.metadata import current_review, validate_anchors
    access = AuthorizationService(session)
    scope = access.require_retrieval_scope(principal=principal)
    docs = list(session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == principal.tenant_id,
        KnowledgeDocument.knowledge_base_id.in_(scope.knowledge_base_ids)).order_by(KnowledgeDocument.id)))
    original = read(corpus_manifest)
    expected = {Path(row['file']).name: row['sha256'] for row in original['documents']}
    if len(docs) != 13 or {row.file_name for row in docs} != set(expected):
        raise ValueError('当前可读语料与13份已冻结原件清单不符。')
    result = []
    for doc in docs:
        reviewed = current_review(doc)
        if doc.status != 'ready' or not is_published(doc) or reviewed is None or doc.content_hash != expected[doc.file_name]:
            raise ValueError('文档发布、原件或制造复核不符。')
        validate_anchors(session, doc, reviewed.data)
        result.append(dict(id=str(doc.id), knowledge_base_id=str(doc.knowledge_base_id), file_name=doc.file_name,
            version=doc.version, content_hash=doc.content_hash, metadata_sha256=digest(doc.metadata_json)))
    chunks = session.scalars(select(DocumentChunk).where(DocumentChunk.tenant_id == principal.tenant_id,
        DocumentChunk.knowledge_base_id.in_(scope.knowledge_base_ids))).all()
    return scope, result, [dict(document_id=str(row.document_id), content=row.content) for row in chunks]


def prepare(args, app, client, suite):
    from sqlalchemy import select
    from backend.app.db.model_usage_models import ModelUsageEvent
    from backend.app.manufacturing.evaluation_sources import registry_for
    from backend.app.manufacturing.access import WorkOrderAccessService
    from backend.app.manufacturing.applicability import ApplicabilityResolver
    from backend.app.manufacturing.answer_context import comparison_question
    from backend.app.manufacturing.service import public_applicability
    from backend.app.evaluation.metrics import contains_fact
    from backend.app.quality.verification import runtime_config
    if (args.run_dir / 'prepared.json').exists() or (args.run_dir / 'attempt.json').exists():
        raise ValueError('已有冻结输入或执行标记，拒绝覆盖。')
    admin_token, subject = token(args.tokens, 'admin', app)
    other_token, _ = token(args.tokens, 'unauthorized', app)
    headers = {'Authorization': 'Bearer ' + admin_token}
    for supplied in (admin_token, other_token):
        response = client.get('/api/knowledge-bases', headers={'Authorization': 'Bearer ' + supplied})
        if response.status_code != 200:
            raise ValueError('零模型身份预检失败。')
    prepared, reads = [], []
    zero = EvaluationBudget(dict(LIMITS, max_model_calls=0, max_tokens=0))
    llm = app.state.answer_service._base_llm
    if llm.max_retries != 0 or llm.request_timeout > 120:
        raise ValueError('模型重试或超时配置超出本轮边界。')
    with app.state.database_session_factory() as session:
        principal = principal_for(session, subject)
        scope, docs, chunks = inventory(session, principal, args.corpus_manifest)
        connector = app.state.mes_connectors[principal.tenant_id]
        access = WorkOrderAccessService(session, connector)
        registry = registry_for(app.state.settings)
        business_scope = access.scope(principal)
        for spec in suite['cases']:
            selected = spec['selection']; wo = selected['work_order_id']; values = dict(work_order_id=wo)
            if selected.get('operation_code'):
                response = client.get('/api/manufacturing/work-orders/' + wo + '/operations', headers=headers)
                if response.status_code != 200:
                    raise ValueError('工序列表读取失败。')
                rows = [row for row in response.json()['items'] if row['operation_code'] == selected['operation_code']]
                if len(rows) != 1:
                    raise ValueError('工序代码不唯一，禁止擅选子行。')
                values['operation_id'] = rows[0]['operation_id']
            if selected.get('job_card_id'):
                values['job_card_id'] = selected['job_card_id']
            elif selected.get('only_job_card'):
                response = client.get('/api/manufacturing/work-orders/' + wo + '/operations/' + values['operation_id'] + '/job-cards', headers=headers)
                if response.status_code != 200 or len(response.json().get('items', [])) != 1 or response.json().get('next_cursor'):
                    raise ValueError('作业卡不是唯一可选记录。')
                values['job_card_id'] = response.json()['items'][0]['job_card_id']
            query = {k: v for k, v in values.items() if k != 'work_order_id'}
            started = perf_counter()
            response = client.get('/api/manufacturing/work-orders/' + wo + '/context' + ('?' + urlencode(query) if query else ''), headers=headers)
            reads.append(dict(case_id=spec['id'], status=response.status_code, elapsed_ms=round((perf_counter()-started)*1000, 3)))
            if response.status_code != 200:
                raise ValueError('冻结来源读取失败。')
            async def current_context():
                return await access.read_context(principal=principal, **values)
            context = client.portal.call(current_context)
            if context.payload_sha256 != response.json()['payload_sha256']:
                raise ValueError('冻结期间来源变化。')
            source = registry.check(session, principal, context=context)
            decision = ApplicabilityResolver().resolve(context=context, scope=scope, business_scope=business_scope,
                session=session, include_other_revisions=comparison_question(spec['question']))
            if spec.get('expected_applicability_revision') and decision.selected_business_revision != spec['expected_applicability_revision']:
                raise ValueError('设计选版标准与真实源/规则不符。')
            for evidence in spec.get('expected_evidence', []):
                ids = {row['id'] for row in docs if row['file_name'] == evidence['document']}
                if not any(row['document_id'] in ids and contains_fact(row['content'], evidence['text']) for row in chunks):
                    raise ValueError('评分摘录不在冻结原文中。')
            prepared.append(dict(**spec, request=dict(question=spec['question'], **values, knowledge_base_id=None,
                expected_source_revision=context.record.source_revision), business_context_binding=dict(
                context=context.model_dump(mode='json'), source=source), applicability_binding=dict(
                catalog_sha256=decision.scope.manufacturing.catalog_sha256, applicability=public_applicability(decision))))
        for _ in range(4):
            values = prepared[0]['request']; query = {k: values[k] for k in ('operation_id', 'job_card_id') if values.get(k)}
            started = perf_counter(); response = client.get('/api/manufacturing/work-orders/'+values['work_order_id']+'/context?'+urlencode(query), headers=headers)
            reads.append(dict(status=response.status_code, elapsed_ms=round((perf_counter()-started)*1000, 3)))
            if response.status_code != 200 or response.json()['payload_sha256'] != prepared[0]['business_context_binding']['context']['payload_sha256']:
                raise ValueError('重复来源预检不稳定。')
        baseline_ids = [str(value) for value in session.scalars(select(ModelUsageEvent.id).where(ModelUsageEvent.tenant_id == principal.tenant_id))]
    if zero.calls:
        raise ValueError('预检发生了模型调用。')
    frozen = dict(schema_version='erpnext-prepared-v2', scorer_version=SCORER_VERSION,
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        suite_sha256=hashlib.sha256(args.suite.read_bytes()).hexdigest(),
        corpus_manifest_sha256=hashlib.sha256(args.corpus_manifest.read_bytes()).hexdigest(),
        runtime=runtime_config(app.state.settings), principal=dict(user_id=str(principal.user_id), tenant_id=str(principal.tenant_id), subject=subject),
        inventory=docs, chunks=len(chunks), source_reads=reads, source_model_calls=zero.calls, baseline_ledger_ids=baseline_ids, cases=prepared)
    write(args.run_dir/'prepared.json', frozen)
    print(json.dumps(dict(status='prepared',cases=16,source_reads=len(reads),model_calls=0,prepared_sha256=hashlib.sha256((args.run_dir/'prepared.json').read_bytes()).hexdigest())),flush=True)


def score_case(case, body, snapshot, trace, status):
    from backend.app.evaluation.manufacturing_metrics import score_manufacturing, same_value as legacy_same_value
    from backend.app.manufacturing.execution_contracts import context_from_payload
    from backend.app.manufacturing.answer_context import ManufacturingAnswerContext, business_citation
    def same_value(field, left, right):
        if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
            return list(left) == list(right)
        return legacy_same_value(field, left, right)
    events = trace.get('stages', {}).get('model_usage', [])
    if case.get('expected_http_status', 200) != 200:
        leaked = any(value in json.dumps(body, ensure_ascii=False) for value in ('ERP-WO-EXECUTION','PO-JOB00008','HC-A17','2026-09-28'))
        return dict(automatic_proxy_pass=status == case['expected_http_status'] and not events and not leaked,
            expected_http_status=case['expected_http_status'], model_calls=len(events), business_disclosed=leaked)
    if status != 200:
        return dict(automatic_proxy_pass=False, error='http_error')
    frozen = case['business_context_binding']['context']
    observed = snapshot['snapshot']['manufacturing']
    failures = []
    if (observed['business_context']['record'] != frozen['record'] or observed['business_context']['payload_sha256'] != frozen['payload_sha256']
            or observed['validation'] != dict(source_verified=True,document_scope_verified=True,business_citations_verified=True,mes_reads=3)):
        failures.append('frozen_source_or_three_rechecks')
    # Acquisition/verification timestamps are observations, not business truth.
    # Only these two metadata fields are aligned to the captured request. Source
    # records, labels, values, expected version and criteria remain frozen.
    current = deepcopy(frozen)
    for key in ('fetched_at', 'last_verified_at'):
        current[key] = observed['business_context'][key]
    context = context_from_payload(current)
    options = {item.field_name: business_citation(item, context) for item in ManufacturingAnswerContext(context, None).items}
    scoring = deepcopy(case)
    scoring['business_context_binding']['context'] = current
    scoring['expected_business_evidence'] = [options[field] for field in case['business_fields']]
    result = score_manufacturing(scoring, body, (snapshot['snapshot'].get('context') or {}).get('items', []),
        execution_scorer=SCORER_VERSION)
    actual = body.get('work_order_context') or {}
    for key, value in frozen['record'].items():
        if key not in ('tenant_id','schema_version') and not same_value(key,actual.get(key),value):
            failures.append('business_record:'+key)
    for field in case['business_fields']:
        expected = options[field]
        if not any(all(same_value(key, row.get(key), value) for key,value in expected.items()
                if key not in ('citation_id','last_verified_at')) for row in body.get('business_citations', [])):
            failures.append('required_business_citation:'+field)
    if observed['applicability'] != case['applicability_binding']['applicability']:
        failures.append('frozen_applicability')
    if not case['expected_model_path'] and events:
        failures.append('unexpected_model_call_on_boundary')
    result.update(erpnext_failures=sorted(set(failures)), volatile_timestamp_policy='captured_request_metadata_only')
    result['automatic_proxy_pass'] = bool(result['automatic_proxy_pass'] and not failures)
    return result


def execute(args, app, client, suite):
    from sqlalchemy import select
    from uuid import UUID
    from backend.app.db.model_usage_models import ModelUsageEvent
    from backend.app.manufacturing.execution_contracts import context_from_payload
    from backend.app.manufacturing.evaluation_sources import registry_for
    from backend.app.quality.verification import runtime_config
    from backend.app.model_usage import summarize_usage
    frozen_path = args.run_dir/'prepared.json'; frozen = read(frozen_path)
    if (frozen.get('schema_version') != 'erpnext-prepared-v2' or frozen.get('scorer_version') != SCORER_VERSION
            or frozen.get('runner_sha256') != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()):
        raise ValueError('入口或评分版本与本轮冻结记录不符。')
    if (frozen['suite_sha256'] != hashlib.sha256(args.suite.read_bytes()).hexdigest()
            or frozen['corpus_manifest_sha256'] != hashlib.sha256(args.corpus_manifest.read_bytes()).hexdigest()
            or frozen['runtime'] != runtime_config(app.state.settings)):
        raise ValueError('题集或应用指纹变化，禁止启动。')
    attempt = args.run_dir/'attempt.json'
    if attempt.exists():
        raise ValueError('本批已有执行标记；不重试、不续跑。')
    if not args.allow_model_calls:
        raise ValueError('执行须显式允许本批真实模型调用。')
    # Validate identity/source before committing the one-shot execution marker.
    admin, subject = token(args.tokens, 'admin', app)
    if subject != frozen['principal']['subject']:
        raise ValueError('执行身份与冻结身份不同。')
    with app.state.database_session_factory() as session:
        principal = principal_for(session, subject)
        _, current_docs, _ = inventory(session, principal, args.corpus_manifest)
        if current_docs != frozen['inventory']:
            raise ValueError('冻结语料已变化。')
        for case in frozen['cases']:
            registry_for(app.state.settings).check(session, principal, expected=case['business_context_binding']['source'],
                context=context_from_payload(case['business_context_binding']['context']))
    write(attempt, dict(started_at=datetime.now(timezone.utc).isoformat(),limits=LIMITS,scorer_version=SCORER_VERSION,
        prepared_sha256=hashlib.sha256(frozen_path.read_bytes()).hexdigest(),runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        allow_model_calls=True,request_retries=0,continuations=False))
    output = args.run_dir/'execution';output.mkdir();(output/'traces').mkdir();(output/'snapshots').mkdir()
    budget = BatchBudget(dict(LIMITS)); records=[]; all_events=[]; stopped=None
    for case in frozen['cases']:
        if stopped:
            records.append(dict(case_id=case['id'],status='not_executed',reason=stopped));continue
        try:
            budget.check()
            supplied,_ = token(args.tokens,case.get('role','admin'),app)
            def guard(case=case):
                with app.state.database_session_factory() as session:
                    current = principal_for(session, frozen['principal']['subject'])
                    _, docs, _ = inventory(session,current,args.corpus_manifest)
                    if docs != frozen['inventory']:
                        raise ValueError('冻结语料变化。')
                    registry_for(app.state.settings).check(session,current,expected=case['business_context_binding']['source'],
                        context=context_from_payload(case['business_context_binding']['context']))
            budget.guard=guard
            if case.get('role','admin')=='admin':guard()
        except Exception as exc:
            stopped='pre_request_'+type(exc).__name__
            records.append(dict(case_id=case['id'],status='not_executed',reason=stopped));continue
        def ask():
            with evaluation_budget(budget),capture_trace() as trace:
                response=client.post('/api/manufacturing/qa',json=case['request'],headers={'Authorization':'Bearer '+supplied})
            return response,trace.to_dict()
        started=perf_counter();budget.request_deadline=monotonic()+180
        executor=ThreadPoolExecutor(max_workers=1);future=executor.submit(ask)
        timed_out=False
        try:
            response,trace=future.result(timeout=min(180,max(1,LIMITS['max_seconds']-(monotonic()-budget.started))))
        except FutureTimeout:
            budget.limited_by='request_time';stopped='request_timeout';timed_out=True
            write(output/(case['id']+'-timeout.json'),dict(case_id=case['id'],status='timeout',request=case['request']))
            # No new request follows. Retain eventual in-flight usage/response.
            response,trace=future.result()
        finally:
            executor.shutdown(wait=True)
        body=response.json();events=trace.get('stages',{}).get('model_usage',[]);all_events.extend(events)
        row=dict(case_id=case['id'],status='timeout' if timed_out else response.status_code,http_status=response.status_code,
            request=case['request'],response=body,request_id=response.headers.get('x-request-id'),elapsed_ms=round((perf_counter()-started)*1000,3),model_calls=len(events))
        # Raw response is durable before snapshots or scoring; never overwrite it.
        write(output/(case['id']+'-response.json'),row);write(output/'traces'/(case['id']+'.json'),trace)
        try:
            snapshot={}
            if response.status_code==200:
                observed=client.get('/api/qa/runs/'+body['run_id'],headers={'Authorization':'Bearer '+supplied})
                if observed.status_code!=200:raise ValueError('成功回答无可读快照。')
                snapshot=observed.json();write(output/'snapshots'/(case['id']+'.json'),snapshot)
            row['metrics']=score_case(case,body,snapshot,trace,response.status_code)
            if timed_out:row['metrics']['automatic_proxy_pass']=False
            row['response_binding']=digest(dict(case=case,response=body,snapshot=snapshot))
        except Exception as exc:
            row['metrics']=dict(automatic_proxy_pass=False,scoring_error=type(exc).__name__)
        records.append(row)
        write(output/(case['id']+'-evaluation.json'),row)
        print(json.dumps(dict(case_id=case['id'],status=row['status'],passed=row['metrics']['automatic_proxy_pass'],model_calls=len(events),budget=budget.snapshot())),flush=True)
        if budget.limited_by:stopped=budget.limited_by
    with app.state.database_session_factory() as session:
        rows=session.scalars(select(ModelUsageEvent).where(ModelUsageEvent.tenant_id==UUID(frozen['principal']['tenant_id']),
            ModelUsageEvent.id.not_in([UUID(value) for value in frozen['baseline_ledger_ids']]))).all()
        ledger=[dict(event_id=str(row.id),request_id=row.request_id,stage=row.stage,provider=row.provider,model=row.model,status=row.status,
            total_tokens=row.total_tokens,estimated_cost=str(row.estimated_cost) if row.estimated_cost is not None else None,details=row.details) for row in rows]
    write(output/'independent-ledger.json',ledger)
    trace_ids={event['event_id'] for event in all_events};ledger_ids={event['event_id'] for event in ledger}
    charged=sum(row['total_tokens'] if row['total_tokens'] is not None else LIMITS['reservation_tokens'] for row in ledger)
    reconciliation=dict(matched=trace_ids==ledger_ids and budget.calls==len(ledger) and budget.charged_tokens==charged,
        trace_calls=len(trace_ids),ledger_calls=len(ledger),ledger_budget_tokens=charged,pending=sum(row['status']=='pending' for row in ledger))
    write(output/'usage.json',dict(usage=summarize_usage(all_events),budget=budget.snapshot(),reconciliation=reconciliation))
    write(output/'records.json',records)
    report=dict(scorer_version=SCORER_VERSION,total=16,executed=sum(row['status']!='not_executed' for row in records),automatic_proxy_passed=sum(row.get('metrics',{}).get('automatic_proxy_pass',False) for row in records),
        model_exercised_cases=sum(row.get('model_calls',0)>0 for row in records),zero_model_cases=sum(row['status']!='not_executed' and row.get('model_calls',0)==0 for row in records),
        stopped=stopped,budget=budget.snapshot(),reconciliation=reconciliation,semantic_review='pending',old_results_combined=False,request_retries=0,continuations=False)
    write(output/'report.json',report);print(json.dumps(report),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    phase=parser.add_mutually_exclusive_group(required=True);phase.add_argument('--prepare',action='store_true');phase.add_argument('--execute',action='store_true')
    parser.add_argument('--suite',type=Path,required=True);parser.add_argument('--tokens',type=Path,required=True);parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--corpus-manifest',type=Path,required=True)
    parser.add_argument('--allow-model-calls',action='store_true');args=parser.parse_args()
    args.run_dir.mkdir(parents=True,exist_ok=True);suite=load_suite(args.suite)
    from fastapi.testclient import TestClient
    from backend.app.main import app
    with TestClient(app) as client:
        if args.prepare:
            with evaluation_budget(EvaluationBudget(dict(LIMITS,max_model_calls=0,max_tokens=0))):prepare(args,app,client,suite)
        else:execute(args,app,client,suite)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        import traceback
        print(json.dumps(dict(status='failed',error_type=type(exc).__name__,frames=[dict(file=Path(x.filename).name,line=x.lineno,function=x.name) for x in traceback.extract_tb(exc.__traceback__)])),flush=True)
        raise SystemExit(1) from None
