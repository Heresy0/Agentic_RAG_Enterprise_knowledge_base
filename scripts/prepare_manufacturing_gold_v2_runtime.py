"""Update only three corrected originals in a new gold100 v2 clone."""
import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app.main import app
from backend.app.config import Settings
from backend.app.db.models import KnowledgeDocument,DocumentIndexingJob
from backend.app.evaluation.trace import capture_trace
from backend.app.indexing.document_indexing_worker import DocumentIndexingWorker
from backend.app.quality.eval_budget import EvaluationBudget,evaluation_budget
from scripts.prepare_manufacturing_gold_runtime import read,write,request
from scripts.manufacturing_gold_v2_live import LIMITS,NOTE,ROOT


def require_v2():
    settings=app.state.settings
    if (not settings.database_url.rsplit('/',1)[-1].startswith('gold100_v2_')
            or not settings.milvus_collection.startswith('gold100_v2_')):
        raise ValueError('Only the new v2 clone may be changed.')


def stage(client,args):
    rows=[r for r in read(ROOT/'sample_docs/manufacturing_zh_corrections_v2/sources.json')['documents']
          if r['repository_path'].startswith('sample_docs/manufacturing_zh_corrections_v2/')]
    assert len(rows)==3
    write(args.run_dir/'upload-attempt.json',dict(documents=3,model_calls=0))
    kb=read(args.run_dir/'new-knowledge-base.json')['id']
    staged=[]
    for row in rows:
        path=ROOT/row['repository_path']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
        with app.state.database_session_factory() as session:
            doc=session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id==UUID(kb),
                KnowledgeDocument.file_name==path.name))
            assert doc is not None and doc.status=='ready' and doc.version==1
            old_id,old_hash=str(doc.id),doc.content_hash
        with path.open('rb') as stream:
            response=request(client,args.tokens,'POST',f'/api/knowledge-bases/{kb}/documents',files={'file':(path.name,stream)})
        assert response['document']['id']==old_id
        staged.append(dict(source_id=row['id'],repository_path=row['repository_path'],sha256=row['sha256'],
            old_sha256=old_hash,knowledge_base_id=kb,document_id=old_id,response=response))
    with app.state.database_session_factory() as session:
        jobs=session.scalars(select(DocumentIndexingJob).where(DocumentIndexingJob.status.in_(['queued','retrying','running']))).all()
        assert len(jobs)==3 and {str(j.document_id) for j in jobs}=={r['document_id'] for r in staged}
        for job in jobs:job.max_attempts=1
        session.commit()
    write(args.run_dir/'staged-v2.json',staged)
    print(json.dumps(dict(phase='stage-v2',documents=3,model_calls=0)))


def index(args):
    write(args.run_dir/'index-attempt.json',dict(limits=LIMITS['index'],retries=0,documents=3))
    expected={UUID(r['document_id']) for r in read(args.run_dir/'staged-v2.json')}
    budget=EvaluationBudget(LIMITS['index'])
    settings=Settings(milvus_access='write')
    try:
        with app.state.database_session_factory() as session:
            jobs=session.scalars(select(DocumentIndexingJob).where(DocumentIndexingJob.status.in_(['queued','retrying','running']))).all()
            assert len(jobs)==3 and {j.document_id for j in jobs}==expected
        for number in range(3):
            with app.state.database_session_factory() as session,evaluation_budget(budget),capture_trace() as trace:
                budget.check()
                worked=DocumentIndexingWorker(session=session,settings=settings).run_once()
                jobs=session.scalars(select(DocumentIndexingJob).where(DocumentIndexingJob.document_id.in_(expected))).all()
                failed=[str(j.id) for j in jobs if j.status not in ('queued','succeeded')]
                row=dict(index=number+1,worked=worked,failed_jobs=failed,budget=budget.snapshot(),trace=trace.to_dict())
                with (args.run_dir/'index-events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
                if not worked or failed:raise RuntimeError('Index attempt failed; no retry or continuation.')
                print(json.dumps({k:v for k,v in row.items() if k!='trace'}),flush=True)
        write(args.run_dir/'indexed.json',dict(documents=3,budget=budget.snapshot()))
    finally:
        write(args.run_dir/'index-budget-final.json',budget.snapshot())


def review(client,args):
    result=[]
    for row in read(args.run_dir/'staged-v2.json'):
        path=f"/api/knowledge-bases/{row['knowledge_base_id']}/documents/{row['document_id']}"
        page=request(client,args.tokens,'GET',path+'/review',params=dict(include_parents='true',limit=50))
        doc=page['document'];assert doc['version']==2 and doc['status']=='ready'
        publication=request(client,args.tokens,'PUT',path+'/publication',json=dict(expected_version=2,
            expected_revision=doc['publication']['revision'],state='published',acknowledge_ocr=True,review_note=NOTE))
        result.append(dict(source_id=row['source_id'],document_id=row['document_id'],publication=publication))
    write(args.run_dir/'reviewed-v2.json',result)
    print(json.dumps(dict(phase='review-v2',published=3,model_calls=0)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['stage','index','review'])
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--tokens',type=Path,required=True)
    args=parser.parse_args()
    with TestClient(app) as client:
        require_v2()
        if args.phase=='index':index(args)
        else:globals()[args.phase](client,args)


if __name__=='__main__':main()
