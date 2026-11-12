"""One-shot execution helpers for the frozen gold100 candidate (not a human gold signoff).

Only questions and ordinary application selections enter QA. Gold answers/reviews
remain offline. Faults change real data exclusively in the dedicated MES clone.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import monotonic
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.main import app
from backend.app.db.models import KnowledgeBase
from backend.app.db.model_usage_models import ModelUsageEvent
from backend.app.evaluation.trace import capture_trace
from backend.app.quality.eval_budget import EvaluationBudget, evaluation_budget, EvaluationStopped
from scripts.accept_erpnext_development_v2 import token, principal_for
from scripts.prepare_manufacturing_gold_runtime import isolated, read, write, NOTE
from scripts.manufacturing_gold_fixture_plan import LIMITS

ROOT = Path(__file__).resolve().parents[1]
SELECTION_KEYS = ('work_order_id', 'operation_id', 'job_card_id', 'batch_id', 'step_id')


def selection(raw):
    """The source ID routes to a platform, never an extra trusted client field."""
    return {k: str(raw[k]) for k in SELECTION_KEYS if raw.get(k) is not None}


def resolve_alias(raw, aliases):
    if not raw.get('source_system_id', '').startswith('ISOLATED-MES-'):
        return selection(raw)
    entry = aliases[raw['work_order_id']]
    assert entry['source_system_id'] == 'openmes-hc'
    return dict(work_order_id=entry['work_order_id'], batch_id=entry['batch_id'], step_id=entry['step_id'])


class RunBudget(EvaluationBudget):
    def __init__(self, state):
        super().__init__(LIMITS['evaluation'])
        self.deadline = datetime.fromisoformat(state['started_at']).timestamp() + self.limits['max_seconds']
        self.calls = state['calls']; self.charged_tokens = state['charged_tokens']
        self.unknown_calls = state['unknown_calls']; self.limited_by = state.get('limited_by')

    def check(self):
        if datetime.now(timezone.utc).timestamp() >= self.deadline:
            self.limited_by = 'time'
        if self.limited_by:
            raise EvaluationStopped(self.limited_by)

    def before_call(self):
        if self.deadline - datetime.now(timezone.utc).timestamp() < 125:
            self.limited_by = 'time_reserve'
        super().before_call()

    def snapshot(self):
        value = super().snapshot()
        value['elapsed_seconds'] = round(datetime.now(timezone.utc).timestamp() - self.deadline + self.limits['max_seconds'], 3)
        return value


class Runtime:
    def __init__(self, client, args, budget):
        self.client, self.args, self.budget, self.events = client, args, budget, []

    def call(self, method, path, *, role='admin', **kwargs):
        bearer, _ = token(self.args.tokens, role, app)
        before = self.budget.calls
        with evaluation_budget(self.budget), capture_trace() as trace:
            response = self.client.request(method, path, headers={'Authorization': 'Bearer ' + bearer}, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = dict(non_json_status=response.status_code)
        row = dict(method=method, path=path, status_code=response.status_code, response=body,
                   request_id=response.headers.get('X-Request-ID'), trace=trace.to_dict(),
                   model_calls=self.budget.calls - before)
        self.events.append(row)
        with (self.args.run_dir / 'request-events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
        if method == 'POST' and path in ('/api/qa', '/api/manufacturing/qa') and body.get('run_id'):
            snapshot = self.call('GET', '/api/qa/runs/' + str(body['run_id']))
            row['snapshot_read_status'] = snapshot['status_code']
        return row

    def require(self, method, path, **kwargs):
        row = self.call(method, path, **kwargs)
        if row['status_code'] >= 400:
            raise RuntimeError(f"Fixture request failed: {row['status_code']} {path}")
        return row['response']


def mapping_path(sel, kb):
    return (f"/api/manufacturing/work-orders/{sel['work_order_id']}/batches/{sel['batch_id']}"
            f"/steps/{sel['step_id']}/guidance-mapping?knowledge_base_id={kb}")


def advance_mapping(rt, sel, kb, *, document_id=None, operation_code=None, withdraw=False):
    path = mapping_path(sel, kb)
    current = rt.require('GET', path)
    if withdraw:
        return rt.require('PUT', path, json=dict(expected_revision=current['revision'], state='withdrawn', review_note=NOTE))
    candidates = rt.require('GET', path.replace('?', '/candidates?'))
    if document_id is None:
        previous = current['mapping']['data']
        document_id, operation_code = previous['document_id'], previous['operation_code']
    assert any(r['document_id'] == document_id and r['operation_code'] == operation_code for r in candidates['items'])
    body = dict(expected_revision=current['revision'], state='draft', document_id=document_id,
                operation_code=operation_code, expected_source_revision=candidates['source_revision'], review_note=NOTE)
    draft = rt.require('PUT', path, json=body)
    return rt.require('PUT', path, json=dict(body, state='reviewed', expected_revision=draft['revision']))


def bind_answer(rt, sel, kb):
    context = rt.require('GET', f"/api/manufacturing/work-orders/{sel['work_order_id']}/context",
                         params={k: v for k, v in sel.items() if k != 'work_order_id'})
    conversation = rt.require('POST', '/api/manufacturing/conversations',
        json=dict(sel, knowledge_base_id=kb, expected_source_revision=context['source_revision']))
    question = '所选步骤实际开工时间是什么？' if 'step_id' in sel else '所选作业卡实际开工时间是什么？'
    answer = rt.require('POST', '/api/manufacturing/qa', json=dict(sel, knowledge_base_id=kb,
                         conversation_id=conversation['id'], question=question))
    if not answer['answerable']:
        raise ValueError('Protocol prerequisite did not produce an actual bound answer.')
    return conversation['id'], answer


def context_for(rt, sel):
    return rt.require('GET', f"/api/manufacturing/work-orders/{sel['work_order_id']}/context",
                      params={k: v for k, v in sel.items() if k != 'work_order_id'})


@contextmanager
def no_knowledge_scope():
    """Remove only the cloned database's active retrieval scope, then restore it."""
    with app.state.database_session_factory() as session:
        rows = session.scalars(select(KnowledgeBase)).all()
        before = {r.id: r.status for r in rows}
        for row in rows:
            row.status = 'disabled'
        session.commit()
    try:
        yield
    finally:
        with app.state.database_session_factory() as session:
            for key, status in before.items():
                session.get(KnowledgeBase, key).status = status
            session.commit()


@contextmanager
def source_change(args):
    """Wrap a REAL read, then PATCH the actual new OpenMES database before re-read.

    No source payload/authorization is replaced. This controller credential is
    private and separate from the platform's strictly read-only adapter token.
    """
    import httpx
    cfg = read(args.control)
    if cfg['upstream_container'] != 'gold100-upstream-mes-20261112-r1':
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


def maps(rt, args):
    aliases = read(args.aliases)
    docs = {r['source_id']: r for r in read(args.run_dir / 'staged.json')}
    from scripts.manufacturing_zh_expansion_data import PRODUCTS
    receipts = []
    for p in PRODUCTS:
        alias = aliases[p['work_order']]
        for i, op in enumerate(p['ops']):
            sel = dict(work_order_id=alias['work_order_id'], batch_id=alias['batch_id'], step_id=alias['steps'][i])
            context = context_for(rt, sel)
            assert context['product_model'] == p['model']
            doc = docs[f"ZH-{p['family']}-{2+i*2:02d}"]
            mapping = advance_mapping(rt, sel, doc['knowledge_base_id'], document_id=doc['document_id'], operation_code=op['code'])
            receipts.append(dict(selection=sel, context=context, mapping=mapping))
    write(args.run_dir / 'mapped.json', receipts)


def mapping_kb(sel):
    from backend.app.db.business_models import StepGuidanceRevision
    with app.state.database_session_factory() as session:
        row = session.scalar(select(StepGuidanceRevision).where(
            StepGuidanceRevision.work_order_id == sel['work_order_id'],
            StepGuidanceRevision.batch_id == sel['batch_id'],
            StepGuidanceRevision.step_id == sel['step_id']).order_by(StepGuidanceRevision.revision.desc()))
        return str(row.knowledge_base_id) if row else None


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
    from scripts.manufacturing_gold_fixture_plan import make_plan
    from backend.app.security.authorization import AuthorizationService
    from backend.app.documents.publication import is_published
    from backend.app.db.models import KnowledgeDocument, DocumentChunk
    from urllib.parse import urlsplit, unquote
    source = next(iter(app.state.mes_connectors.values())).config.source_system_id
    platform = 'mes' if source == 'openmes-hc' else 'erp'
    aliases = read(args.aliases)
    kb = read(args.run_dir / 'new-knowledge-base.json')['id']
    all_cases = {r['id']: r for r in (json.loads(s) for s in
        (ROOT / 'evals/manufacturing_gold_100_v1/cases.jsonl').read_text(encoding='utf-8').splitlines())}
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


def contracts(case, row):
    body = row['response']; status = row['status_code']
    expected_status = case['expected']['http_status'] or 200
    checks = dict(http_status=status == expected_status)
    if status == 200 and int(case['id'][-3:]) != 40:
        checks['answerable'] = body.get('answerable') == case['expected']['answerable']
        if case['expected']['behavior'] in ('clarify', 'refuse'):
            checks['response_type'] = body.get('response_type') == case['expected']['behavior']
        if case['expected']['answerable']:
            checks['has_answer'] = bool(body.get('answer', '').strip())
            checks['has_source'] = bool(body.get('citations') or body.get('business_citations'))
    if status >= 400:
        checks['no_answer_leak'] = not any(body.get(k) for k in ('answer', 'citations', 'business_citations', 'work_order_context'))
    return dict(checks=checks, passed=all(checks.values()), semantic_score=False,
                semantic_review='requires_assistant_non_independent_per_fact_review')


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
            finally:
                case = next(json.loads(line) for line in (ROOT / 'evals/manufacturing_gold_100_v1/cases.jsonl').read_text(encoding='utf-8').splitlines()
                            if json.loads(line)['id'] == args.case_id)
                out = dict(case_id=args.case_id, final=result, harness_error=error, events=rt.events,
                           budget=budget.snapshot(), contract=contracts(case, result) if result else dict(passed=False),
                           request_retries=0)
                write(args.run_dir / (args.case_id + '-result.json'), out)
                print(json.dumps(dict(case_id=args.case_id, status=result['status_code'] if result else None,
                                     contract=out['contract']['passed'], budget=out['budget'], error=error)), flush=True)


if __name__ == '__main__':
    main()
