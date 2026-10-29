"""Execute read-only inside the upgraded API container; no model calls."""
import json,sys
from collections import Counter
from uuid import UUID
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from backend.app.config import Settings
from backend.app.db.session import create_database_engine
from backend.app.db.models import KnowledgeDocument,DocumentChunk,DocumentParent,DocumentIndexingJob
from backend.app.knowledge.rag import CHUNKING_VERSION
from backend.app.documents.chunk_quality import CLEANING_VERSION
from backend.app.documents.publication import is_published
from backend.app.knowledge.milvus_store import MilvusVectorStore
from scripts.validate_manufacturing_public import parser_fingerprint
tid=UUID(sys.argv[1]);settings=Settings();db=create_database_engine(settings.database_url);store=MilvusVectorStore(settings)
records=[]
with Session(db) as session:
    docs=session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id==tid)).all()
    jobs=session.scalars(select(DocumentIndexingJob).where(DocumentIndexingJob.tenant_id==tid)).all()
    assert len(docs)==18 and len(jobs)==18
    assert all(j.status in ('succeeded','failed') for j in jobs)
    for d in docs:
        chunks=session.scalars(select(DocumentChunk).where(DocumentChunk.document_id==d.id)).all()
        parents=session.scalar(select(func.count()).select_from(DocumentParent).where(DocumentParent.document_id==d.id))
        count=store.count('tenant_id == '+json.dumps(str(tid))+' and document_id == '+json.dumps(str(d.id)))
        assert count==len(chunks)
        if d.status=='ready':
            assert is_published(d)
            assert chunks and d.metadata_json['chunking_version']==CHUNKING_VERSION
            assert d.metadata_json['quality']['cleaning']['trace_count']>0
            assert all(c.metadata_json.get('cleaning_version')==CLEANING_VERSION and c.metadata_json['cleaning']['traces'] for c in chunks)
            assert len({c.vector_generation for c in chunks})==1
        else:assert d.status=='failed' and d.file_name.startswith('MFG-ZH11_') and not chunks and count==0 and parents==0
        records.append(dict(source_id=d.file_name.split('_')[0],document_id=str(d.id),knowledge_base_id=str(d.knowledge_base_id),
            file_name=d.file_name,status=d.status,sha256=d.content_hash,version=d.version,chunk_count=len(chunks),vector_count=count,
            parent_count=parents,parser_counts=dict(Counter(c.metadata_json.get('parser') for c in chunks)),metadata=d.metadata_json))
    usage_model_config={k:v for k,v in settings.__dict__.items() if k in ('embedding_model','embedding_dimension','rerank_model','llm_model','model_name','conversation_memory_enabled','pdf_ocr_enabled','qa_snapshots_enabled')}
db.dispose()
print(json.dumps(dict(tenant_id=str(tid),chunking_version=CHUNKING_VERSION,cleaning_version=CLEANING_VERSION,parser_sha256=parser_fingerprint(),
    all_checks_passed=True,documents=len(records),ready_documents=sum(r['status']=='ready' for r in records),
    chunks=sum(r['chunk_count'] for r in records),vectors=sum(r['vector_count'] for r in records),parents=sum(r['parent_count'] for r in records),
    jobs=dict(Counter(j.status for j in jobs)),model_config=usage_model_config,records=sorted(records,key=lambda r:r['source_id'])),ensure_ascii=False))
