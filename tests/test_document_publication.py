"""Publication is checked on SQL candidates, evidence refresh and raw sources."""
import hashlib
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from test_document_upload_api import api_context, _set_principal, _upload_url
from backend.app.api.document_review import router
from backend.app.db.models import DocumentChunk, KnowledgeDocument
from backend.app.documents.publication import is_published, publication, require_review
from backend.app.knowledge.chunk_repository import ChunkRepository
from backend.app.security.retrieval_scope import RetrievalScope


def indexed(context, *, ocr=False):
    context.app.include_router(router)
    _set_principal(context, context.editor)
    content=b'calibration source'
    with TestClient(context.app) as client:
        r=client.post(_upload_url(context),files={'file':('policy.txt',content,'text/plain')})
        assert r.status_code==202
        identifier=UUID(r.json()['document']['id'])
    with context.session_factory() as s:
        doc=s.get(KnowledgeDocument,identifier)
        path=context.storage_dir/str(context.tenant_id)/str(context.knowledge_base_id)/'policy.txt'
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
        doc.storage_uri=path.as_uri();doc.status='ready'
        doc.metadata_json={**doc.metadata_json,'chunk_count':1,'quality':{'review_required':ocr},'chunking_version':'structured-v7'}
        chunk=DocumentChunk(id='c'*64,tenant_id=doc.tenant_id,knowledge_base_id=doc.knowledge_base_id,document_id=doc.id,
            chunk_index=0,content='校准前必须预热。',content_hash='h'*64,
            keyword_text='校准 预热',chunking_version='structured-v7',embedding_model='test',vector_generation='g'*32,
            metadata_json={'section_path':'要求','page_start':2,'page_end':2})
        s.add(chunk);s.commit()
    return identifier


def urls(context,identifier):return f'{_upload_url(context)}/{identifier}'


def test_draft_publish_refresh_and_withdraw_use_same_sql_gate(api_context):
    c=api_context;identifier=indexed(c);base=urls(c,identifier)
    scope=RetrievalScope(c.tenant_id,knowledge_base_id=c.knowledge_base_id)
    with c.session_factory() as s:
        repo=ChunkRepository(s);assert repo.list_chunks(scope=scope)==[]
    with TestClient(c.app) as client:
        preview=client.get(base+'/review');assert preview.status_code==200
        doc=preview.json()['document'];assert doc['publication']['state']=='draft'
        assert client.get(base+'/chunks/'+'c'*64).status_code==404
        payload=dict(expected_version=1,expected_revision=doc['publication']['revision'],state='published',review_note='核对原件与适用范围')
        assert client.put(base+'/publication',json=payload).status_code==200
        assert client.put(base+'/publication',json=payload).status_code==409
        assert client.get(base+'/chunks/'+'c'*64).status_code==200
        with c.session_factory() as s:
            repo=ChunkRepository(s);rows=repo.list_chunks(scope=scope);assert len(rows)==1
            before={rows[0].chunk_id:rows[0].metadata}
        payload.update(expected_revision=doc['publication']['revision']+1,state='withdrawn',review_note='需补充校准条件')
        assert client.put(base+'/publication',json=payload).status_code==200
        with c.session_factory() as s:
            assert ChunkRepository(s).current_chunks(scope=scope,expected=before)==[]
        assert client.get(base+'/chunks/'+'c'*64).status_code==404


def test_viewer_cannot_review_publish_or_read_draft_source(api_context):
    c=api_context;identifier=indexed(c);base=urls(c,identifier);_set_principal(c,c.viewer)
    with TestClient(c.app) as client:
        assert client.get(base+'/review').status_code==403
        assert client.get(base+'/source?version=1').status_code==403
        assert client.put(base+'/publication',json=dict(expected_version=1,expected_revision=1,state='published',review_note='test')).status_code==403
        assert client.get(urls(c,uuid4())+'/review').status_code==403


def test_ocr_release_requires_acknowledgment_and_keeps_warning(api_context):
    c=api_context;identifier=indexed(c,ocr=True);base=urls(c,identifier)
    with TestClient(c.app) as client:
        doc=client.get(base+'/review').json()['document']
        payload=dict(expected_version=1,expected_revision=doc['publication']['revision'],state='published',review_note='已逐页检查')
        assert client.put(base+'/publication',json=payload).status_code==422
        payload['acknowledge_ocr']=True
        result=client.put(base+'/publication',json=payload);assert result.status_code==200
        assert result.json()['quality']['review_required'] is True


def test_source_checks_version_and_hash_and_legacy_stays_readable(api_context):
    c=api_context;identifier=indexed(c);base=urls(c,identifier)
    with c.session_factory() as s:
        doc=s.get(KnowledgeDocument,identifier);doc.metadata_json={k:v for k,v in doc.metadata_json.items() if k!='publication'}
        assert is_published(doc) and publication(doc)['state']=='legacy_published';s.commit()
    _set_principal(c,c.viewer)
    with TestClient(c.app) as client:
        r=client.get(base+'/source?version=1');assert r.status_code==200 and r.content==b'calibration source'
        assert r.headers['cache-control']=='no-store'
        assert client.get(base+'/source?version=2').status_code==409
        with c.session_factory() as s:
            doc=s.get(KnowledgeDocument,identifier);doc.content_hash='a'*64;s.commit()
        assert client.get(base+'/source?version=1').status_code==409


def test_reindex_resets_publication_without_altering_source_quality(api_context):
    c=api_context;identifier=indexed(c)
    with c.session_factory() as s:
        doc=s.get(KnowledgeDocument,identifier);original=doc.metadata_json['quality']
        doc.metadata_json={**doc.metadata_json,'publication':{'state':'published','revision':4}}
        require_review(doc)
        assert not is_published(doc) and publication(doc)['revision']==5
        assert doc.metadata_json['quality']==original
