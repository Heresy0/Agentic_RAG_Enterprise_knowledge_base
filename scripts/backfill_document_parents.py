"""Prepare parents from existing SQL child text, with zero embedding calls."""
import argparse
import json
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def backfill(*, apply=False, document_ids=()):
    from sqlalchemy import select
    from backend.app.config import Settings
    from backend.app.db.models import KnowledgeDocument, DocumentChunk
    from backend.app.db.session import create_database_engine, create_session_factory
    from backend.app.documents.parent_chunks import build_parent_records, publish_parents
    settings=Settings()
    engine=create_database_engine(settings.database_url)
    records=[]
    try:
        with create_session_factory(engine)() as session:
            query=select(KnowledgeDocument.id).where(KnowledgeDocument.status=='ready')
            if document_ids:
                query=query.where(KnowledgeDocument.id.in_(document_ids))
            identifiers=list(session.scalars(query.order_by(KnowledgeDocument.id)))
            session.rollback()
            for identifier in identifiers:
                doc=session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id==identifier,
                    KnowledgeDocument.status=='ready').with_for_update())
                if doc is None:
                    session.rollback()
                    continue
                children=session.scalars(select(DocumentChunk).where(
                    DocumentChunk.tenant_id==doc.tenant_id,DocumentChunk.knowledge_base_id==doc.knowledge_base_id,
                    DocumentChunk.document_id==doc.id).order_by(DocumentChunk.chunk_index)).all()
                count=publish_parents(session,doc,children) if apply else len(build_parent_records(doc,children))
                records.append(dict(document_id=str(doc.id),file_name=doc.file_name,document_version=doc.version,
                    children=len(children),parents=count,status='published' if apply else 'planned'))
                session.commit() if apply else session.rollback()
    finally:
        engine.dispose()
    return dict(apply=apply,documents=len(records),parents=sum(r['parents'] for r in records),
                embedding_calls=0,vector_writes=0,records=records)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--document-ids',nargs='+',type=UUID)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    result=backfill(apply=args.apply,document_ids=args.document_ids or ())
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='records'}))
