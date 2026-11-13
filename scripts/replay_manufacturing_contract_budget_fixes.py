"""Local replay of saved boundary/context regressions; no model, SQL or network."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from scripts.diagnose_manufacturing_selection_failures import (
    ROOT, read, digest, normal, manufacturing_answer_context,
)
from scripts.diagnose_manufacturing_grounded_regressions import draft_for, check, repair_bytes, qa
from backend.app.knowledge import retrieval_service as retrieval_module
from backend.app.knowledge.chunk_repository import ChunkRepository, StoredChunk
from backend.app.knowledge.keyword_repository import PgKeywordRepository
from backend.app.knowledge.retrieval_service import RetrievalService
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.reranker import BaseReranker, RerankResult
from backend.app.manufacturing.source_statements import execution_boundary_verdict
from backend.app.security.retrieval_scope import RetrievalScope
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever

OLD = ROOT/'tmp/manufacturing-gold-100-v4-selection-live-20261113-r2'
NEW = ROOT/'tmp/manufacturing-gold-100-v4-grounded-live-20261113-r5'
GOLD = ROOT/'evals/manufacturing_gold_100_v4'
NUMBERS = (36, 91, 95)


class UnusedEmbeddings(Embeddings):
    def embed_documents(self, texts):
        raise AssertionError('Replay must not embed documents.')

    def embed_query(self, text):
        raise AssertionError('Replay reuses saved fusion, not embeddings.')


def replay_search(old, new, question):
    a=qa(old)['trace']['stages']; b=qa(new)['trace']['stages']
    signature=lambda rows:[(r['chunk_id'],r['content']) for r in rows]
    assert signature(a['policy_candidates'])==signature(b['policy_candidates'])
    assert signature(a['rerank_model'])==signature(b['rerank_model'])
    frozen=b['policy_candidates']
    documents=[Document(id=r['chunk_id'],page_content=r['content'],
        metadata={k:v for k,v in r.items() if k not in ('rank','content')}) for r in frozen]
    expected_ids=[r['chunk_id'] for r in a['rerank']]
    parents=a['parent_context']; calls=[]

    class FrozenRepository(ChunkRepository):
        def __init__(self, *args):
            pass

        def has_ready_chunks(self, **kwargs):
            return True

        def candidate_policies(self, **kwargs):
            return {}

        def parent_chunks(self, *, scope, expected):
            # The SQL parent resolver is unchanged. Its saved result is usable
            # only when the actual new search asks for the exact old anchors.
            assert list(expected)==expected_ids
            calls.append(list(expected))
            return [StoredChunk(chunk_id=r['chunk_id'],document_id=UUID(r['document_id']),
                tenant_id=UUID(r['tenant_id']),knowledge_base_id=UUID(r['knowledge_base_id']),
                content=r['content'],document_name=r['document_name'],source=r['document_name'],
                metadata={k:v for k,v in r.items() if k not in ('rank','content')}) for r in parents]

    class FrozenKeywords(PgKeywordRepository):
        def __init__(self, *args):
            pass

        def search(self, *args, **kwargs):
            raise AssertionError('Replay must not query SQL.')

    class FrozenRanking(BaseReranker):
        def rerank(self, *, query, documents, top_n):
            assert len(documents)==len(frozen) and top_n==len(frozen)
            ids={r['chunk_id']:i for i,r in enumerate(frozen)}
            return [RerankResult(index=ids[r['chunk_id']],relevance_score=r['rerank_score'])
                    for r in b['rerank_model']]

    class FrozenFusion(BaseRetriever):
        documents: list[Document]

        def _get_relevant_documents(self, query, *, run_manager):
            return self.documents

    scope=RetrievalScope(tenant_id=UUID(frozen[0]['tenant_id']),
                        knowledge_base_id=UUID(frozen[0]['knowledge_base_id']))
    service=RetrievalService(SimpleNamespace(rag_parent_context_enabled=True,rag_rerank_top_k=12),
                            embeddings=UnusedEmbeddings(),reranker=FrozenRanking(),vector_store=object())
    with patch.object(retrieval_module,'ChunkRepository',FrozenRepository), \
         patch.object(retrieval_module,'PgKeywordRepository',FrozenKeywords), \
         patch.object(retrieval_module,'create_hybrid_retriever',return_value=FrozenFusion(documents=documents)):
        selected=service.search(question,scope=scope,session=object())
    context=ContextBuilder().build(selected,question=question)
    expected=[(r['citation_id'],r['document_name'],r['content']) for r in a['context']]
    actual=[(r.citation_id,r.document_name,r.content) for r in context.items]
    assert actual==expected
    assert context.incomplete_reason is None
    return context,dict(top_k=12,ranked_anchors=len(expected_ids),parent_resolver_calls=len(calls),
        returned_parents=len(selected),context_items=len(context.items),
        exact_old_context_content_order_citations=True,incomplete_reason=context.incomplete_reason,
        limitation='Saved fusion/ranking and exact-anchor SQL parent results are substitutes. No live scope/source/SQL recheck; missing trace metadata prevents exact formatted character-count claims.')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    out=parser.parse_args().out.resolve();out.relative_to(ROOT/'tmp');out.mkdir(exist_ok=False)
    cases={r['id']:r for r in map(json.loads,(GOLD/'cases.jsonl').read_text(encoding='utf-8').splitlines())}
    protected=[run/(f'MFG-GOLD-{n:03}-result.json') for run in (OLD,NEW) for n in NUMBERS]
    protected += [GOLD/name for name in ('cases.jsonl','sources.json','review_policy.json','manifest.json')]
    protected += list((ROOT/'evals/manufacturing_gold_100_grounded_live_v1/runs/20261113-r5').glob('*'))
    code=['manufacturing/source_statements.py','knowledge/retrieval_service.py','knowledge/answer_repair.py',
          'knowledge/context_builder.py','knowledge/chunk_repository.py','knowledge/answer_validation.py']
    protected += [ROOT/'backend/app'/name for name in code]
    protected += [Path(__file__).resolve(),ROOT/'scripts/diagnose_manufacturing_grounded_regressions.py',
                  ROOT/'scripts/diagnose_manufacturing_selection_failures.py']
    before={str(p.relative_to(ROOT)).replace('\\','/'):digest(p) for p in protected}
    rows=[]
    for number in NUMBERS:
        key=f'MFG-GOLD-{number:03}';case=cases[key]
        old=read(OLD/(key+'-result.json'));new=read(NEW/(key+'-result.json'))
        if number==36:
            context,active,draft,gen=draft_for(new,case);original=draft.model_dump()
            old_items=qa(old)['trace']['stages']['context']
            restored=context.items[:len(old_items)]
            assert [(x.citation_id,x.document_name,x.content) for x in restored] == [
                (x['citation_id'],x['document_name'],x['content']) for x in old_items]
            restored_context=replace(context,items=restored)
            with manufacturing_answer_context(active):
                validation=check(draft,context,case['question']);assert validation['valid']
                restored_validation=check(draft,restored_context,case['question']);assert restored_validation['valid']
                rendered=AnswerService._render_result(None,draft=draft,context=restored_context,question=case['question'])
                boundary=execution_boundary_verdict(case['question'],draft,restored_context)
            assert original==draft.model_dump()
            assert rendered.answer.startswith('仅凭实际开工登记，不能认定')
            rows.append(dict(case_id=key,recorded_errors=gen['validation_errors'],replayed_validation=validation,
                restored_five_items_validation=restored_validation,restored_items_match_old_content_order_citations=True,
                frozen_draft_unchanged=True,boundary=boundary,rendered_answer=rendered.answer,
                business_citations=rendered.business_citations,
                limitation='Contract replay only; unchanged broad Completed wording still requires semantic review. Not a new question-level pass.'))
        else:
            context,proof=replay_search(old,new,case['question'])
            row=dict(case_id=key,search_replay=proof)
            if number==91:
                previous_context,_,draft,gen=draft_for(new,case)
                row['original_draft_validation']=check(draft,context,case['question'])
                assert not row['original_draft_validation']['valid']
                row['repair_prompt_bytes_before']=repair_bytes(draft,previous_context,case['question'],gen['validation_errors'])
                row['repair_prompt_bytes_after']=repair_bytes(draft,context,case['question'],gen['validation_errors'])
                assert row['repair_prompt_bytes_before']==26344 and row['repair_prompt_bytes_after']==16703
                row['repair_limit_bytes']=24000;row['existing_repair_budget_eligible']=True
                row['limitation']='The saved out-of-range index remains invalid. This fixes access to the existing repair, not its hypothetical model output.'
            else:
                _,_,known_draft,_=draft_for(old,case)
                row['known_old_draft_compatibility']=check(known_draft,context,case['question'])
                assert row['known_old_draft_compatibility']['valid']
                row['target_quote_in_retained_context']=all(any(normal(e['quote']) in normal(x.content)
                    for x in context.items) for e in case['evidence'] if e['kind']=='text')
                assert row['target_quote_in_retained_context']
                row['limitation']='Generation gate is no longer blocked; the old draft is only a compatibility fixture, not a new answer or score.'
            rows.append(row)
    assert all(digest(ROOT/name)==value for name,value in before.items())
    result=dict(schema_version='manufacturing-contract-budget-offline-replay-v1',cases=rows,
        model_calls=0,network_connections_allowed=False,api_calls=0,database_calls=0,container_operations=0,
        inputs_unchanged=True,input_sha256=before,old_scores_recomputed=False,new_accuracy_claimed=False)
    with (out/'evidence.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(cases=len(rows),protected_inputs=len(before),inputs_unchanged=True,model_calls=0,network=False)))


if __name__=='__main__':
    main()
