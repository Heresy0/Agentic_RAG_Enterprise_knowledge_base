"""Prepare/import the Chinese corpus only inside a separately cloned gold100 database.

Administrative fixture writes do not constitute factory approval. Model indexing is
single attempt, budgeted independently from question evaluation, and never retried.
Run inside an isolated application container with its normal authentication stack.
"""
import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, func
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.config import Settings
from backend.app.db.models import (KnowledgeBase, KnowledgeBaseUserGrant,
    KnowledgeDocument, DocumentChunk, DocumentParent, DocumentIndexingJob)
from backend.app.quality.eval_budget import EvaluationBudget, evaluation_budget
from scripts.accept_erpnext_development_v2 import token, principal_for
from scripts.manufacturing_gold_fixture_plan import LIMITS
from scripts.initialize_manufacturing_phase1 import quote_anchor

ROOT = Path(__file__).resolve().parents[1]
NOTE = '助手非独立复核：仅在百题隔离演示夹具发布原文，不是人工或工厂批准。'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)


def isolated(settings):
    if not settings.database_url.rsplit('/', 1)[-1].startswith('gold100_'):
        raise ValueError('Only an independent gold100 database may be modified.')
    if not settings.milvus_collection.startswith('gold100_'):
        raise ValueError('Only an independent gold100 vector collection is allowed.')


def request(client, tokens, method, path, **kwargs):
    bearer, _ = token(tokens, 'admin', app)
    response = client.request(method, path, headers={'Authorization': 'Bearer ' + bearer}, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f'{method} {path}: {response.status_code} {response.text[:1000]}')
    return response.json()


def stage(client, args):
    if (args.run_dir / 'staged.json').exists():
        raise ValueError('Existing upload receipt must not be overwritten.')
    bearer, subject = token(args.tokens, 'admin', app)
    rows = [r for r in read(ROOT / 'sample_docs/manufacturing_zh_expansion_v1/sources.json')['documents']
            if not r['id'].startswith('BASE-')]
    assert len(rows) == 42
    with app.state.database_session_factory() as session:
        p = principal_for(session, subject)
        marker = args.run_dir / 'new-knowledge-base.json'
        if marker.exists():
            kb_id = read(marker)['id']
            assert session.scalar(select(func.count()).select_from(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_base_id == UUID(kb_id))) == 0
            assert not (args.run_dir / 'upload-events.jsonl').exists()
        else:
            kb = KnowledgeBase(id=uuid4(), tenant_id=p.tenant_id, name='百题隔离中文扩展语料-r1')
            session.add(kb); session.flush()
            session.add(KnowledgeBaseUserGrant(tenant_id=p.tenant_id, knowledge_base_id=kb.id,
                                              user_id=p.user_id, permission='admin'))
            session.commit(); kb_id = str(kb.id)
            write(marker, dict(id=kb_id, fixture_only=True))
    result = []
    for row in rows:
        path = ROOT / row['repository_path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        with path.open('rb') as stream:
            data = request(client, args.tokens, 'POST', f'/api/knowledge-bases/{kb_id}/documents',
                           files={'file': (path.name, stream)})
        result.append(dict(source_id=row['id'], repository_path=row['repository_path'],
                           sha256=row['sha256'], knowledge_base_id=kb_id, document_id=data['document']['id'],
                           response=data))
        with (args.run_dir / 'upload-events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(result[-1], ensure_ascii=False) + '\n')
    # A one-shot worker may only process this newly uploaded inventory; disable job retries.
    with app.state.database_session_factory() as session:
        jobs = session.scalars(select(DocumentIndexingJob).where(
            DocumentIndexingJob.knowledge_base_id == UUID(kb_id))).all()
        assert len(jobs) == 42 and all(j.attempt_count == 0 for j in jobs)
        for job in jobs:
            job.max_attempts = 1
        session.commit()
    write(args.run_dir / 'staged.json', result)
    print(json.dumps(dict(stage='staged', documents=len(result), model_calls=0)), flush=True)


def index(args):
    from backend.app.indexing.document_indexing_worker import DocumentIndexingWorker
    from backend.app.evaluation.trace import capture_trace
    write(args.run_dir / 'index-attempt.json', dict(limits=LIMITS['index'], retries=0))
    budget = EvaluationBudget(LIMITS['index'])
    staged = read(args.run_dir / 'staged.json')
    expected = {UUID(r['document_id']) for r in staged}
    settings = Settings(milvus_access='write')
    try:
        with app.state.database_session_factory() as session:
            pending = session.scalars(select(DocumentIndexingJob).where(
                DocumentIndexingJob.status.in_(['queued', 'retrying', 'running']))).all()
            if len(pending) != 42 or {j.document_id for j in pending} != expected:
                raise ValueError('Worker queue differs from the 42 frozen new documents.')
        for i in range(42):
            budget.check()
            with app.state.database_session_factory() as session, evaluation_budget(budget), capture_trace() as trace:
                worked = DocumentIndexingWorker(session=session, settings=settings).run_once()
                jobs = session.scalars(select(DocumentIndexingJob).where(
                    DocumentIndexingJob.document_id.in_(expected))).all()
                failed = [str(j.id) for j in jobs if j.status not in ('queued', 'succeeded')]
                event = dict(index=i + 1, worked=worked, failed_jobs=failed,
                             budget=budget.snapshot(), trace=trace.to_dict())
                with (args.run_dir / 'index-events.jsonl').open('a', encoding='utf-8') as f:
                    f.write(json.dumps(event, ensure_ascii=False, default=str) + '\n')
                if not worked or failed:
                    raise RuntimeError('One-shot indexing failed; no retry or continuation.')
                print(json.dumps({k: v for k, v in event.items() if k != 'trace'}), flush=True)
        write(args.run_dir / 'indexed.json', dict(status='completed', documents=42, budget=budget.snapshot()))
    finally:
        write(args.run_dir / 'index-budget-final.json', budget.snapshot())


def review(client, args):
    from scripts.manufacturing_zh_expansion_data import PRODUCTS
    staged = read(args.run_dir / 'staged.json')
    sources = {r['id']: r for r in read(ROOT / 'sample_docs/manufacturing_zh_expansion_v1/sources.json')['documents']}
    current = {}
    for p in PRODUCTS:
        for i, op in enumerate(p['ops']):
            current[f"ZH-{p['family']}-{2 + i * 2:02d}"] = (p, op)
    reviewed = []
    for row in staged:
        path = f"/api/knowledge-bases/{row['knowledge_base_id']}/documents/{row['document_id']}"
        page = request(client, args.tokens, 'GET', path + '/review',
                       params=dict(include_parents='true', limit=50))
        assert page['document']['status'] == 'ready' and page['items']
        if row['source_id'] in current:
            p, op = current[row['source_id']]
            # Only actual current instruction files are mapped. Full source anchors
            # are validated by the normal review endpoint, never fabricated IDs.
            code = sources[row['source_id']]['document_code']
            meta = dict(document_family_id=code,
                document_code=code, business_revision='2.0',
                source_kind='formal_document', document_role='instruction', scope_kind='product_operation',
                product_models=[p['model']], operation_codes=[op['code']], plant_codes=['HC-P01'],
                effective_from=p['switch'], source_anchors=[quote_anchor(page['items'])], rules=[])
            body = dict(expected_document_version=page['document']['version'], expected_revision=0,
                        state='draft', metadata=meta, review_note=NOTE)
            draft = request(client, args.tokens, 'PUT', path + '/manufacturing-metadata', json=body)
            response = request(client, args.tokens, 'PUT', path + '/manufacturing-metadata',
                               json=dict(body, state='reviewed', expected_revision=draft['revision']))
            doc = response['document']
        else:
            doc = page['document']
        pub = request(client, args.tokens, 'PUT', path + '/publication', json=dict(
            expected_version=doc['version'], expected_revision=doc['publication']['revision'],
            state='published', acknowledge_ocr=True, review_note=NOTE))
        reviewed.append(dict(source_id=row['source_id'], document_id=row['document_id'],
                             knowledge_base_id=row['knowledge_base_id'], publication=pub))
        with (args.run_dir / 'review-events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(reviewed[-1], ensure_ascii=False) + '\n')
    write(args.run_dir / 'reviewed.json', reviewed)
    print(json.dumps(dict(stage='reviewed', published=len(reviewed), manufacturing_reviews=6, model_calls=0)), flush=True)


def export(args):
    kb_id = UUID(read(args.run_dir / 'new-knowledge-base.json')['id'])
    def values(obj):
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    with app.state.database_session_factory() as session:
        tables = {}
        for cls in (KnowledgeBase, KnowledgeBaseUserGrant, KnowledgeDocument, DocumentParent, DocumentChunk):
            column = cls.id if cls is KnowledgeBase else cls.knowledge_base_id
            tables[cls.__tablename__] = [values(o) for o in session.scalars(select(cls).where(column == kb_id))]
        assert len(tables['documents']) == 42 and len(tables['document_chunks']) == 832
    write(args.run_dir / 'replica.json', dict(knowledge_base_id=str(kb_id), tables=tables))
    print(json.dumps(dict(stage='export', documents=42, chunks=832, model_calls=0)), flush=True)


def import_replica(args):
    from datetime import datetime
    from sqlalchemy import Uuid, DateTime
    replica = read(args.run_dir / 'replica.json')
    with app.state.database_session_factory() as session:
        assert session.get(KnowledgeBase, UUID(replica['knowledge_base_id'])) is None
        for cls in (KnowledgeBase, KnowledgeBaseUserGrant, KnowledgeDocument, DocumentParent, DocumentChunk):
            for row in replica['tables'][cls.__tablename__]:
                for col in cls.__table__.columns:
                    if row[col.name] is not None:
                        if isinstance(col.type, Uuid):
                            row[col.name] = UUID(row[col.name])
                        elif isinstance(col.type, DateTime):
                            row[col.name] = datetime.fromisoformat(row[col.name])
                session.add(cls(**row))
            session.flush()
        session.commit()
    print(json.dumps(dict(stage='replica', documents=42, chunks=832, new_embedding_calls=0)), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=['stage', 'index', 'review', 'export', 'import-replica'])
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--tokens', type=Path, required=True)
    args = p.parse_args(); args.run_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        isolated(app.state.settings)
        if args.phase in ('stage', 'review'):
            globals()[args.phase](client, args)
        elif args.phase == 'import-replica':
            import_replica(args)
        else:
            globals()[args.phase](args)


if __name__ == '__main__':
    main()
