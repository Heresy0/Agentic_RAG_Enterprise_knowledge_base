"""Actual SQL parent publication, isolation, refresh and citation regressions."""
from uuid import uuid4
import pytest
from fastapi import HTTPException, Response
from sqlalchemy import delete, select
from test_document_indexing_service import session, scope_data, _settings, FakeEmbeddings
from fake_vector_store import FakeVectorStore
from backend.app.db.models import DocumentChunk, DocumentParent, KnowledgeDocument, KnowledgeBaseUserGrant
from backend.app.documents.parent_chunks import build_parent_records, publish_parents, body_text
from backend.app.indexing.document_indexing_service import DocumentIndexingService
from backend.app.knowledge.chunk_repository import ChunkRepository
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.milvus_retriever import stored_chunk_to_document
from backend.app.security.retrieval_scope import RetrievalScope
from backend.app.security.principal import Principal
from backend.app.api.knowledge_bases import preview_citation


@pytest.fixture
def indexed(session, scope_data, tmp_path):
    scope=RetrievalScope(scope_data.tenant_id,knowledge_base_id=scope_data.technology_kb_id)
    path=tmp_path/'验收.md'
    path.write_text('# 验收\n\n## 校验\n\n执行两轮校验。\n\n## 保留条件\n\n客户必须书面接受，修复期限为10月18日。',encoding='utf-8')
    embedding=FakeEmbeddings()
    service=DocumentIndexingService(session=session,settings=_settings(),embeddings=embedding,vector_store=FakeVectorStore())
    service.index_file(file_path=path,scope=scope,created_by_user_id=scope_data.user_id)
    doc=session.scalar(select(KnowledgeDocument))
    chunks=session.scalars(select(DocumentChunk).order_by(DocumentChunk.chunk_index)).all()
    return scope,path,service,doc,chunks,embedding


def expected(chunks):
    return {r.id:dict(chunk_id=r.id,document_id=str(r.document_id),knowledge_base_id=str(r.knowledge_base_id),
                      vector_generation=r.vector_generation) for r in chunks}


def test_parent_resolves_complete_text_once_and_preview_matches(session, scope_data, indexed):
    scope,_,_,doc,children,_=indexed
    rows=ChunkRepository(session).parent_chunks(scope=scope,expected=expected(children))
    assert len(rows)==1 and '执行两轮' in rows[0].content and '书面接受' in rows[0].content
    context=ContextBuilder().build([stored_chunk_to_document(r) for r in rows])
    assert len(context.items)==1 and context.items[0].parent_id
    session.add(KnowledgeBaseUserGrant(tenant_id=scope.tenant_id,knowledge_base_id=scope.knowledge_base_id,
        user_id=scope_data.user_id,permission='viewer'))
    session.commit()
    principal=Principal(scope_data.user_id,scope.tenant_id,'keycloak-alice')
    preview=preview_citation(scope.knowledge_base_id,doc.id,rows[0].chunk_id,Response(),
        parent_id=context.items[0].parent_id,principal=principal,session=session)
    assert preview.content==context.items[0].content
    legacy=preview_citation(scope.knowledge_base_id,doc.id,rows[0].chunk_id,Response(),
        parent_id=None,principal=principal,session=session)
    assert legacy.content==children[0].content
    with pytest.raises(HTTPException) as error:
        preview_citation(scope.knowledge_base_id,doc.id,rows[0].chunk_id,Response(),
            parent_id='f'*64,principal=principal,session=session)
    assert error.value.status_code==404


@pytest.mark.parametrize('mutation',['version','file_hash','parent_generation','parent_missing'])
def test_expanded_parent_fails_closed_when_current_evidence_changes(session,indexed,mutation):
    scope,_,_,doc,children,_=indexed
    repo=ChunkRepository(session)
    old=repo.parent_chunks(scope=scope,expected=expected(children))[0]
    parent=session.scalar(select(DocumentParent))
    if mutation=='version':doc.version+=1
    elif mutation=='file_hash':doc.content_hash='f'*64
    elif mutation=='parent_generation':parent.vector_generation='f'*32
    else:session.execute(delete(DocumentParent))
    session.commit()
    assert repo.parent_chunks(scope=scope,expected={old.chunk_id:old.metadata})==[]
    raw=repo.parent_chunks(scope=scope,expected=expected(children))
    assert all('parent_id' not in r.metadata for r in raw)


def test_parent_query_never_crosses_scope_and_inactive_document_is_unreadable(session,scope_data,indexed):
    scope,_,_,doc,children,_=indexed
    repo=ChunkRepository(session)
    for forbidden in (RetrievalScope(scope_data.tenant_id,knowledge_base_id=scope_data.hr_kb_id),
                      RetrievalScope(uuid4(),knowledge_base_id=scope.knowledge_base_id),
                      RetrievalScope(scope.tenant_id,knowledge_base_ids=())):
        assert repo.parent_chunks(scope=forbidden,expected=expected(children))==[]
    doc.status='pending';session.commit()
    assert repo.parent_chunks(scope=scope,expected=expected(children))==[]


def test_update_replaces_parents_atomically_without_extra_embedding_call(session,scope_data,indexed):
    scope,path,service,doc,children,embedding=indexed
    old=ChunkRepository(session).parent_chunks(scope=scope,expected=expected(children))[0]
    before=embedding.document_calls
    publish_parents(session,doc,children);session.commit()
    assert embedding.document_calls==before
    assert session.scalars(select(DocumentParent)).all()[0].id==old.metadata['parent_id']
    path.write_text('# 验收\n\n新版本只需要一轮校验。',encoding='utf-8')
    service.index_file(file_path=path,scope=scope,created_by_user_id=scope_data.user_id)
    assert embedding.document_calls==before+1
    assert ChunkRepository(session).parent_chunks(scope=scope,expected={old.chunk_id:old.metadata})==[]
    parents=session.scalars(select(DocumentParent)).all()
    assert len(parents)==1 and '只需要一轮' in parents[0].content and '书面接受' not in parents[0].content
    session.delete(doc);session.commit()
    assert session.scalars(select(DocumentParent)).all()==[]


def test_long_documents_have_bounded_section_parents_and_keep_authored_labels(indexed):
    _,_,_,doc,children,_=indexed
    assert body_text('文档：标题\n章节：章节\n\n文档：作者实际声明\n正文')=='文档：作者实际声明\n正文'
    rows=[]
    for i in range(10):
        rows.append(DocumentChunk(id=str(i)*64,tenant_id=doc.tenant_id,knowledge_base_id=doc.knowledge_base_id,
            document_id=doc.id,chunk_index=i,content='正文'+str(i)+'文'*400,vector_generation=children[0].vector_generation,
            metadata_json={'section_path':'A' if i<5 else 'B'}))
    parents=build_parent_records(doc,rows)
    assert len(parents)>=4 and all(len(p.content)<=1500 for p in parents)
    assert all(len(p.metadata_json['sections'])==1 for p in parents)
    assert all(any(p.id==row.parent_id for p in parents) for row in rows)
    rows[-1].vector_generation='f'*32
    with pytest.raises(ValueError,match='不同索引版本'):build_parent_records(doc,rows)


def test_failed_publication_rolls_back_parent_and_child_links(session,indexed):
    scope,_,_,doc,children,_=indexed
    before=ChunkRepository(session).parent_chunks(scope=scope,expected=expected(children))[0]
    doc.version+=1
    publish_parents(session,doc,children)
    session.flush()
    session.rollback()
    after=ChunkRepository(session).parent_chunks(scope=scope,expected=expected(children))[0]
    assert before.metadata['parent_id']==after.metadata['parent_id'] and before.content==after.content


def test_forged_child_parent_link_cannot_return_another_knowledge_base(session,scope_data,indexed,tmp_path):
    scope,_,_,_,children,_=indexed
    path=tmp_path/'HR秘密.md'
    path.write_text('# HR\n\n只有HR可以读取的秘密金额为987654元。',encoding='utf-8')
    hr=RetrievalScope(scope.tenant_id,knowledge_base_id=scope_data.hr_kb_id)
    service=DocumentIndexingService(session=session,settings=_settings(),embeddings=FakeEmbeddings(),vector_store=FakeVectorStore())
    service.index_file(file_path=path,scope=hr,created_by_user_id=scope_data.user_id)
    parent=session.scalar(select(DocumentParent).where(DocumentParent.knowledge_base_id==hr.knowledge_base_id))
    children[0].parent_id=parent.id;session.commit()
    rows=ChunkRepository(session).parent_chunks(scope=scope,expected=expected([children[0]]))
    assert len(rows)==1 and 'parent_id' not in rows[0].metadata and '987654' not in rows[0].content


def test_answer_proxy_checks_parent_identity_as_well_as_child_identity():
    from backend.app.evaluation.metrics import score_answer
    case=dict(answerable=True,required_facts=['书面接受'],
              expected_evidence=[dict(document='验收.md',text='客户必须书面接受')])
    context=[dict(citation_id='资料1',document_name='验收.md',chunk_id='c'*64,
                  parent_id='a'*64,content='客户必须书面接受')]
    citation=dict(citation_id='资料1',document_name='验收.md',chunk_id='c'*64,parent_id='b'*64)
    response=dict(answerable=True,answer='客户必须书面接受',citations=[citation])
    assert not score_answer(case,response,context)['automatic_proxy_pass']
    citation['parent_id']='a'*64
    assert score_answer(case,response,context)['automatic_proxy_pass']
