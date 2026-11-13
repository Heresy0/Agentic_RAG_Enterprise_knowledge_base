"""Frozen v4 selection-contract gold100 transport; reuses v1 API helpers, never v1 labels/results."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
from uuid import UUID
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app.main import app
from backend.app.quality.eval_budget import EvaluationBudget,evaluation_budget
from scripts.accept_erpnext_development_v2 import token,principal_for
from scripts.manufacturing_gold_fixture_plan import make_plan as v1_plan,LIMITS as V1_LIMITS
from scripts.manufacturing_gold_live import (Runtime as LegacyRuntime,RunBudget,selection,resolve_alias,read,write,isolated,
    context_for,mapping_path,mapping_kb,advance_mapping,bind_answer,no_knowledge_scope,maps,contracts)
ROOT=Path(__file__).resolve().parents[1]
GOLD=ROOT/'evals/manufacturing_gold_100_v4'
LIMITS=deepcopy(V1_LIMITS)
LIMITS['index']=dict(max_model_calls=0,max_tokens=0,max_seconds=300,reservation_tokens=32000)
NOTE='助手非独立复核：仅用于v4隔离演示验证，不是人工、工厂或平台黄金样本批准。'
assert LIMITS['evaluation']==V1_LIMITS['evaluation']


class Runtime(LegacyRuntime):
    """Keep an already delivered primary response if its snapshot observation fails.

    The case still fails its observation contract; no question is sent again.
    """
    observation_failure = None

    def call(self, method, path, *, role='admin', **kwargs):
        start = len(self.events)
        try:
            return super().call(method,path,role=role,**kwargs)
        except Exception:
            if method == 'POST' and path in ('/api/qa','/api/manufacturing/qa') and len(self.events) > start:
                primary = self.events[start]
                if primary['method'] == method and primary['path'] == path:
                    self.observation_failure = dict(question=kwargs.get('json',{}).get('question'),response=primary)
            raise


def make_plan():
    plan=v1_plan()
    cases=[json.loads(line) for line in (GOLD/'cases.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [c['id'] for c in cases]==[r['case_id'] for r in plan['cases']]
    for case,row in zip(cases,plan['cases']):
        assert case['expected']['behavior']==row['expected_behavior']
        assert case['expected']['http_status']==row['gold_http_status']
    plan.update(schema_version='manufacturing-gold-100-execution-plan-v4',limits=LIMITS,
        gold_cases_sha256=hashlib.sha256((GOLD/'cases.jsonl').read_bytes()).hexdigest(),
        user_authorization='当前引用校验及来源选择修复版与不变v4黄金集隔离完整100题；800调用/500万Token/9000秒，不重试不补跑',dataset='manufacturing_gold_100_v4')
    return plan


@contextmanager
def source_change(args):
    """Wrap a REAL read, then PATCH the actual new OpenMES database before re-read.

    No source payload/authorization is replaced. This controller credential is
    private and separate from the platform's strictly read-only adapter token.
    """
    import httpx
    cfg = read(args.control)
    if cfg['upstream_container'] != 'gold100-v4-selection-upstream-mes-20261113-r2':
        raise ValueError('Fault controller must point at the independent clone.')
    upstream = httpx.Client(base_url=cfg['url'], trust_env=False, timeout=20,
                           headers={'Authorization': 'Bearer ' + cfg['token'], 'Accept': 'application/json'})
    connector = next(iter(app.state.mes_connectors.values()))
    original = connector.read_context
    changed = []; old = {}
    async def read_then_change(*a, **kw):
        context = await original(*a, **kw)
        if not changed:
            wo = context.record.work_order_id
            value = upstream.get('/api/v1/work-orders/' + wo)
            value.raise_for_status(); old.update(wo=wo, quantity=value.json()['data']['planned_qty'])
            result = upstream.patch('/api/v1/work-orders/' + wo, json=dict(planned_qty=float(old['quantity']) + 1))
            result.raise_for_status(); changed.append(True)
        return context
    connector.read_context = read_then_change
    try:
        yield changed
    finally:
        connector.read_context = original
        if changed:
            result = upstream.patch('/api/v1/work-orders/' + old['wo'], json=dict(planned_qty=old['quantity']))
            result.raise_for_status()
        upstream.close()


def probes(rt, args):
    source = next(iter(app.state.mes_connectors.values())).config.source_system_id
    if source == 'openmes-hc':
        alias = read(args.aliases)['DEMO-GOLD-CONTROL']
        sel = selection(alias)
        kb = read(args.run_dir / 'new-knowledge-base.json')['id']
        doc = next(r for r in read(args.run_dir / 'staged.json') if r['source_id'] == 'ZH-B21-02')
        advance_mapping(rt, sel, kb, document_id=doc['document_id'], operation_code='OP-10')
        conversation, _ = bind_answer(rt, sel, kb)
        conflict = rt.call('POST', '/api/manufacturing/qa', json=dict(sel,
            step_id=alias['steps'][1], knowledge_base_id=kb, conversation_id=conversation, question='这一步何时实际开工？'))
        assert conflict['status_code'] == 409 and conflict['model_calls'] == 0
        conversation, _ = bind_answer(rt, sel, kb)
        advance_mapping(rt, sel, kb)
        conflict = rt.call('POST', '/api/manufacturing/qa', json=dict(sel,
            knowledge_base_id=kb, conversation_id=conversation, question='这一步何时实际开工？'))
        assert conflict['status_code'] == 409 and conflict['model_calls'] == 0
        with source_change(args) as changed:
            conflict = rt.call('POST', '/api/manufacturing/qa', json=dict(sel,
                knowledge_base_id=kb, question='所选步骤实际开工时间是什么？'))
            assert changed and conflict['status_code'] == 409 and conflict['model_calls'] == 0
        advance_mapping(rt, sel, kb)
    else:
        sel = dict(work_order_id='ERP-WO-EXECUTION', operation_id='7pvg4cgbg0', job_card_id='PO-JOB00008')
        _, answer = bind_answer(rt, sel, None)
        assert answer['work_order_context']['execution']['job_card_id'] == 'PO-JOB00008'
        unauthorized = rt.call('POST', '/api/manufacturing/qa', role='unauthorized',
            json=dict(sel, question='所选作业卡实际开工时间是什么？'))
        assert unauthorized['status_code'] == 404
        with no_knowledge_scope():
            denied = rt.call('POST', '/api/manufacturing/qa', json=dict(sel, question='所选作业卡实际开工时间是什么？'))
            assert denied['status_code'] == 403
    assert rt.budget.calls == 0
    write(args.run_dir / 'zero-model-probe.json', dict(source=source, requests=len(rt.events),
        budget=rt.budget.snapshot(), events=rt.events, original_environments_modified=False))
    print(json.dumps(dict(phase='probe', source=source, requests=len(rt.events), model_calls=0)))


def freeze(rt, args):

    from backend.app.security.authorization import AuthorizationService
    from backend.app.documents.publication import is_published
    from backend.app.db.models import KnowledgeDocument, DocumentChunk
    from urllib.parse import urlsplit, unquote
    source = next(iter(app.state.mes_connectors.values())).config.source_system_id
    platform = 'mes' if source == 'openmes-hc' else 'erp'
    aliases = read(args.aliases)
    kb = read(args.run_dir / 'new-knowledge-base.json')['id']
    all_cases = {r['id']: r for r in (json.loads(s) for s in
        (ROOT / 'evals/manufacturing_gold_100_v4/cases.jsonl').read_text(encoding='utf-8').splitlines())}
    _, subject = token(args.tokens, 'admin', app)
    with app.state.database_session_factory() as session:
        principal = principal_for(session, subject)
        scope = AuthorizationService(session).require_retrieval_scope(principal=principal)
        docs = session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == principal.tenant_id,
            KnowledgeDocument.knowledge_base_id.in_(scope.knowledge_base_ids))).all()
        assert len(docs) == 55 and all(d.status == 'ready' and is_published(d) for d in docs)
        assert all(hashlib.sha256(Path(unquote(urlsplit(d.storage_uri).path)).read_bytes()).hexdigest() == d.content_hash for d in docs)
        chunks = session.scalars(select(DocumentChunk).where(DocumentChunk.tenant_id == principal.tenant_id,
            DocumentChunk.knowledge_base_id.in_(scope.knowledge_base_ids))).all()
        assert len(chunks) == 885
        expected={Path(d['repository_path']).name:d['sha256'] for d in read(ROOT/'sample_docs/manufacturing_zh_corrections_v2/sources.json')['documents']}
        assert len(expected)==55 and all(expected[d.file_name]==d.content_hash for d in docs)

        inventory = [dict(id=str(d.id), knowledge_base_id=str(d.knowledge_base_id),file_name=d.file_name,
                          content_hash=d.content_hash, version=d.version,
                          metadata_sha256=hashlib.sha256(json.dumps(d.metadata_json,sort_keys=True).encode()).hexdigest()) for d in docs]
    bindings = []
    for plan in make_plan()['cases']:
        if plan['platform'] != platform:
            continue
        case = all_cases[plan['case_id']]
        sel = resolve_alias(case['input']['selection'], aliases)
        row = dict(plan, question=case['question'], selection=sel,
            knowledge_base_id=kb if plan['knowledge_scope'] == 'expanded_chinese' else None,
            resolved_source_system_id=source, context=None, mapping_knowledge_base_id=None)
        if sel:
            row['context'] = context_for(rt, sel)
            checks = []
            for evidence in case['evidence']:
                if evidence['kind'] == 'field' and evidence['source_id'] == case['input']['context_source_id']:
                    actual = row['context']
                    for part in evidence['field_path'].split('.'):
                        actual = actual[part]
                    checks.append(dict(field_path=evidence['field_path'], matches=actual == evidence['value']))
            assert all(r['matches'] for r in checks), case['id'] + ' archived values differ from live fixture'
            row['evidence_field_checks'] = checks
            if 'step_id' in sel:
                row['mapping_knowledge_base_id'] = mapping_kb(sel)
            if plan['requires_alias_resolution']:
                row['alternate_step_id'] = aliases[case['input']['selection']['work_order_id']]['steps'][1]
            if case['id'] == 'MFG-GOLD-035':
                row['foreign_step_id'] = aliases['DEMO-GOLD-CONTROL']['step_id']
            if case['id'] == 'MFG-GOLD-026':
                current = rt.require('GET', mapping_path(sel, row['mapping_knowledge_base_id']))
                assert current['revision'] == 7 and current['mapping']['state'] == 'reviewed'
                data = current['mapping']['data']
                assert data['document_code'] == 'HC-WI-A17-020' and data['business_revision'] == '2.0' and data['operation_code'] == 'OP-20'
                row['initial_mapping'] = current
        bindings.append(row)
    write(args.run_dir / (platform + '-bindings.json'), dict(platform=platform, cases=bindings,
        inventory=inventory, all_readable_knowledge_base_ids=sorted(map(str,scope.knowledge_base_ids)),
        qa_model_calls=0, historical_gold_unchanged=True))
    print(json.dumps(dict(phase='freeze',platform=platform,cases=len(bindings),documents=len(inventory),model_calls=0)))


def run_case(rt, args):
    frozen = read(args.bindings)
    row = next(r for r in frozen['cases'] if r['case_id'] == args.case_id)
    # The original canonical question is used byte-for-byte; runtime aliases and
    # control questions are separately recorded and never counted as extra cases.
    sel, kb, number = row['selection'], row['knowledge_base_id'], int(args.case_id[-3:])
    body = dict(sel, question=row['question'], knowledge_base_id=kb)
    path = '/api/qa' if row['route'] == 'knowledge_qa' else '/api/manufacturing/qa'
    if row['route'] == 'conversation_protocol':
        if number == 39:
            # Earlier deliberate source changes make its old mapping stale.
            # Re-review CURRENT data before binding; history is never rewound.
            advance_mapping(rt, sel, row['mapping_knowledge_base_id'])
        conversation, initial = bind_answer(rt, sel, kb)
        body['conversation_id'] = conversation
        if number == 37:
            body['job_card_id'] = 'PO-JOB00009'
        elif number == 38:
            body.pop('batch_id'); body.pop('step_id')
        elif number in (39, 58, 76, 94):
            advance_mapping(rt, sel, row['mapping_knowledge_base_id'])
        elif number in (57, 75, 93):
            body['step_id'] = row['alternate_step_id']
        elif number == 40:
            # API-level UI selection simulation and historical restoration, not
            # a new browser visual acceptance result.
            context_for(rt, dict(sel, job_card_id='PO-JOB00009'))
            final = rt.call('GET', '/api/manufacturing/conversations/' + conversation)
            rt.require('POST', '/api/manufacturing/qa', json=dict(sel, knowledge_base_id=kb,
                conversation_id=conversation, question='请登记所选作业卡为已开工。'))
            return final
    if number == 28:
        advance_mapping(rt, sel, row['mapping_knowledge_base_id'], withdraw=True)
        try:
            return rt.call('POST', path, json=body)
        finally:
            advance_mapping(rt, sel, row['mapping_knowledge_base_id'])
    if number == 29:
        return rt.call('POST', path, role='unauthorized', json=body)
    if number == 30:
        with no_knowledge_scope():
            return rt.call('POST', path, json=body)
    if number == 33:
        with source_change(args) as changed:
            result = rt.call('POST', path, json=body)
            assert changed, 'The real source mutation must actually occur.'
            return result
    if number == 35:
        body['step_id'] = row['foreign_step_id']
    return rt.call('POST', path, json=body)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=['maps', 'case', 'probe', 'freeze'])
    for key in ('run-dir', 'tokens', 'aliases'):
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--bindings', type=Path); p.add_argument('--budget', type=Path)
    p.add_argument('--case-id'); p.add_argument('--control', type=Path)
    args = p.parse_args()
    args.run_dir.mkdir(parents=True, exist_ok=True)
    budget = RunBudget(read(args.budget)) if args.phase == 'case' else EvaluationBudget(
        dict(max_model_calls=0, max_tokens=0, max_seconds=300, reservation_tokens=32000))
    with TestClient(app) as client:
        isolated(app.state.settings)
        rt = Runtime(client, args, budget)
        if args.phase == 'maps':
            maps(rt, args)
        elif args.phase == 'probe':
            probes(rt, args)
        elif args.phase == 'freeze':
            freeze(rt, args)
        else:
            write(args.run_dir / (args.case_id + '-attempt.json'), dict(case_id=args.case_id, retries=0))
            result = None; error = None
            try:
                budget.check()
                result = run_case(rt, args)
            except Exception as exc:
                # The outer harness records an executed failure, never retries it.
                # Programming/infra failures remain failures instead of becoming answers.
                error = dict(type=type(exc).__name__, message=str(exc)[:800])
                pending = rt.observation_failure
                question = next(r['question'] for r in read(args.bindings)['cases'] if r['case_id'] == args.case_id)
                if pending and pending['question'] == question:
                    result = pending['response']
            finally:
                case = next(json.loads(line) for line in (ROOT / 'evals/manufacturing_gold_100_v4/cases.jsonl').read_text(encoding='utf-8').splitlines()
                            if json.loads(line)['id'] == args.case_id)
                out = dict(case_id=args.case_id, final=result, harness_error=error, events=rt.events,
                           budget=budget.snapshot(), contract=contracts(case, result) if result else dict(passed=False),
                           request_retries=0)
                if result and result.get('response',{}).get('run_id'):
                    out['contract']['checks']['snapshot_observed'] = result.get('snapshot_read_status') == 200
                    out['contract']['passed'] = all(out['contract']['checks'].values()) and error is None
                write(args.run_dir / (args.case_id + '-result.json'), out)
                print(json.dumps(dict(case_id=args.case_id, status=result['status_code'] if result else None,
                                     contract=out['contract']['passed'], budget=out['budget'], error=error)), flush=True)


if __name__=='__main__': main()
