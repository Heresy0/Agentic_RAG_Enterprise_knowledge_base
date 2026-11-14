"""Single fresh seven-case batch, using unchanged business code and API transport.

The gold answers and review policy are used only outside the request. No indexing,
fault injection, fixture approval, retry or previous-score aggregation is allowed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.main import app
from backend.app.quality.eval_budget import EvaluationBudget
from scripts.accept_erpnext_development_v2 import token, principal_for
from scripts.build_manufacturing_gold_100_v3 import CASE_IDS
from scripts.manufacturing_gold_live import Runtime, context_for, contracts, isolated, read, write

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / 'evals/manufacturing_gold_100_v3'
LIMITS = dict(max_model_calls=56,max_tokens=350000,max_seconds=900,reservation_tokens=32000)


class BatchBudget(EvaluationBudget):
    def before_call(self):
        from time import monotonic
        if self.limits['max_seconds'] - (monotonic() - self.started) < 125:
            self.limited_by = 'time_reserve'
        super().before_call()


def payload(row):
    return dict(row['selection'],question=row['question'],knowledge_base_id=row['knowledge_base_id'])


def validate_bindings(frozen):
    cases = {c['id']:c for c in (json.loads(s) for s in (GOLD/'cases.jsonl').read_text(encoding='utf-8').splitlines())}
    if [r['case_id'] for r in frozen['cases']] != list(CASE_IDS) or frozen['limits'] != LIMITS:
        raise ValueError('Only the frozen seven-case batch and its new budget are allowed.')
    for path,digest in frozen['input_hashes'].items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest() != digest:
            raise ValueError('Frozen program or input changed.')
    for r in frozen['cases']:
        c = cases[r['case_id']]
        if r['question'] != c['question'] or r['platform'] != 'erp' or r['route'] not in ('knowledge_qa','manufacturing_qa'):
            raise ValueError('Question, platform or route differs from the frozen batch.')
        if r['case_id'] != 'MFG-GOLD-019' and r['selection']:
            raise ValueError('Document questions cannot impersonate live business selections.')
    return cases


def inventory(tokens):
    from urllib.parse import unquote,urlsplit
    from backend.app.db.models import KnowledgeDocument,DocumentChunk,DocumentIndexingJob
    from backend.app.documents.publication import is_published
    from backend.app.security.authorization import AuthorizationService
    from sqlalchemy import func
    _,subject = token(tokens,'admin',app)
    expected = {Path(r['repository_path']).name:r['sha256'] for r in
        read(ROOT/'sample_docs/manufacturing_zh_corrections_v2/sources.json')['documents']}
    with app.state.database_session_factory() as session:
        scope = AuthorizationService(session).require_retrieval_scope(principal=principal_for(session,subject))
        docs = session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == scope.tenant_id,
            KnowledgeDocument.knowledge_base_id.in_(scope.knowledge_base_ids))).all()
        if len(docs) != 55 or any(d.status != 'ready' or not is_published(d) for d in docs):
            raise ValueError('Expected the unchanged 55 published source documents.')
        rows = []
        for d in docs:
            actual = hashlib.sha256(Path(unquote(urlsplit(d.storage_uri).path)).read_bytes()).hexdigest()
            if actual != d.content_hash or expected.get(d.file_name) != actual:
                raise ValueError('Original differs from the frozen corpus.')
            rows.append(dict(id=str(d.id),knowledge_base_id=str(d.knowledge_base_id),file_name=d.file_name,
                content_hash=d.content_hash,version=d.version,
                metadata_sha256=hashlib.sha256(json.dumps(d.metadata_json,sort_keys=True).encode()).hexdigest()))
        count = session.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.tenant_id == scope.tenant_id,
            DocumentChunk.knowledge_base_id.in_(scope.knowledge_base_ids)))
        active = session.scalar(select(func.count()).select_from(DocumentIndexingJob).where(
            DocumentIndexingJob.status.in_(['queued','retrying','running'])))
        if count != 885 or active:
            raise ValueError('Chunk inventory changed or an indexing job is active.')
    return dict(documents=sorted(rows,key=lambda r:r['id']),chunks=count,active_index_jobs=active)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=['preflight','run','audit'])
    p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--tokens',type=Path,required=True)
    p.add_argument('--bindings',type=Path,required=True);args=p.parse_args()
    frozen=read(args.bindings);cases=validate_bindings(frozen)
    args.run_dir.mkdir(parents=True,exist_ok=True)
    if args.phase == 'run':
        write(args.run_dir/'evaluation-attempt.json',dict(started_at=datetime.now(timezone.utc).isoformat(),
            cases=list(CASE_IDS),limits=LIMITS,request_retries=0,continuations=False))
    with TestClient(app) as client:
        isolated(app.state.settings)
        if app.state.settings.database_url.rsplit('/',1)[-1] != 'gold100_v3_erp7_20261112_r1':
            raise ValueError('This entry may only use its new independent database.')
        before=inventory(args.tokens)
        if args.phase != 'run':
            budget=EvaluationBudget(dict(max_model_calls=0,max_tokens=0,max_seconds=300,reservation_tokens=32000))
            rt=Runtime(client,args,budget)
            row=next(r for r in frozen['cases'] if r['case_id'] == 'MFG-GOLD-019')
            current=context_for(rt,row['selection'])
            original=row['context']
            if any(current[k] != original[k] for k in ('source_revision','payload_sha256','execution')):
                raise ValueError('Selected ERP source changed since the frozen input.')
            write(args.run_dir/(args.phase+'.json'),dict(inventory=before,context=current,events=rt.events,budget=budget.snapshot()))
            print(json.dumps(dict(phase=args.phase,documents=55,chunks=885,source_checks=len(rt.events),model_calls=budget.calls)))
            return
        expected=read(args.run_dir/'preflight.json')['inventory']
        if before != expected:
            raise ValueError('Inventory changed since preflight.')
        budget=BatchBudget(LIMITS);rt=Runtime(client,args,budget);records=[]
        for row in frozen['cases']:
            if budget.limited_by:
                break
            write(args.run_dir/(row['case_id']+'-attempt.json'),dict(case_id=row['case_id'],request_retries=0))
            start=len(rt.events);final=None;error=None
            try:
                budget.check()
                final=rt.call('POST','/api/qa' if row['route'] == 'knowledge_qa' else '/api/manufacturing/qa',json=payload(row))
            except Exception as exc:
                # The outer evaluation boundary preserves a failure, never retries.
                error=dict(type=type(exc).__name__,message=str(exc)[:800])
            result=dict(case_id=row['case_id'],final=final,harness_error=error,events=rt.events[start:],
                contract=contracts(cases[row['case_id']],final) if final else dict(passed=False),budget=budget.snapshot(),request_retries=0)
            write(args.run_dir/(row['case_id']+'-result.json'),result);records.append(result)
            print(json.dumps(dict(case_id=row['case_id'],http=final['status_code'] if final else None,
                contract=result['contract']['passed'],budget=budget.snapshot(),harness_error=error)),flush=True)
        write(args.run_dir/'evaluation-results.json',records)
        write(args.run_dir/'evaluation-finished.json',dict(executed=len(records),expected=7,stopped=budget.limited_by,
            started_at=read(args.run_dir/'evaluation-attempt.json')['started_at'],finished_at=datetime.now(timezone.utc).isoformat(),
            budget=budget.snapshot(),request_retries=0,continuations=False,old_results_combined=False))


if __name__ == '__main__':
    main()
