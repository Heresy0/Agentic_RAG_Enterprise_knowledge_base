"""Controlled live pressure probes using current, authorized, frozen evidence.

This isolates context selection. It is not a new end-to-end retrieval benchmark.
"""
import argparse
from copy import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=ROOT/'evals/system_config.local.json')
    parser.add_argument('--allow-model-calls', action='store_true')
    args = parser.parse_args(argv)
    if not args.allow_model_calls:
        parser.error('Explicit --allow-model-calls required')
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError('Choose a new empty output directory')
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env.evaluation', override=True)
    from backend.app.config import Settings
    from backend.app.db.session import create_database_engine, create_session_factory
    from backend.app.evaluation.runner import verified_principal, token_from_environment
    from backend.app.evaluation.trace import capture_trace, record_documents
    from backend.app.knowledge.answer_service import AnswerService
    from backend.app.knowledge.context_builder import ContextBuilder
    from backend.app.knowledge.evidence_coordinator import CollectedEvidence
    from backend.app.knowledge.milvus_retriever import stored_chunk_to_document
    from backend.app.knowledge.chunk_repository import ChunkRepository
    from backend.app.knowledge.parent_context_selection import BudgetedParentSelector
    from backend.app.knowledge.retrieval_service import RetrievalService
    from backend.app.model_usage import usage_context, parse_prices
    from backend.app.security.authorization import AuthorizationService
    from scripts.evaluate_multihop_comparison import corpus_snapshot

    source_protocol=json.loads((args.source_run/'protocol.json').read_text(encoding='utf-8'))
    cases={c['id']:c for c in json.loads((args.source_run/'frozen-criteria.json').read_text(encoding='utf-8'))['cases']}
    observed={r['id']:r for r in map(json.loads,(args.source_run/'full_parent/traces.jsonl').read_text(encoding='utf-8').splitlines())}
    # Declared from structural eligibility, never selected using answer outcomes.
    probes=[('XH-021','13_'),('XH-D-26','13_'),('XH-011','05_'),('XH-019','11_')]
    selected=[]
    for identifier, prefix in probes:
        anchor=next(d for d in observed[identifier]['stages']['authorized_final'] if d['document_name'].startswith(prefix))
        selected.append(dict(case=cases[identifier],anchor=anchor))
    settings=Settings()
    settings.model_usage_prices=parse_prices(json.dumps(source_protocol['prices']))
    # Both arms isolate construction/generation, with no additional retrieval/planning.
    settings.rag_planner_mode='off'
    settings.rag_feedback_enabled=False
    protocol=dict(kind='controlled_frozen_evidence_pressure_not_end_to_end',started_at=datetime.now(timezone.utc).isoformat(),
        source_run=str(args.source_run),source_head=source_protocol['git_head'],prices=source_protocol['prices'],
        price_source=source_protocol['price_source'],budget_rule='same rendered full-parent size minus 40 characters in both arms',
        evidence_rule='one current SQL-rehydrated parent from a source-run authorized anchor; generation unchanged',
        disabled_in_both=['query planner','evidence feedback','additional retrieval/agent collection'],probes=[])
    (out/'frozen-criteria.json').write_text(json.dumps(dict(cases=[x['case'] for x in selected]),ensure_ascii=False,indent=2),encoding='utf-8')
    config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    engine=create_database_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as session:
            profile=config['profiles']['全范围测试用户']
            principal=verified_principal(settings,session,token_from_environment(profile['token_env']))
            auth=AuthorizationService(session)
            readable=auth.list_readable_knowledge_base_ids(principal=principal)
            if readable!={UUID(config['knowledge_bases'][g]) for g in profile['expected_groups']}:
                raise ValueError('Effective scope differs')
            protocol['corpus_before']=corpus_snapshot(session,principal,readable)
            scope=auth.require_retrieval_scope(principal=principal,knowledge_base_id=None)
            prepared=[]
            for probe in selected:
                anchor=probe['anchor'];case=probe['case']
                documents=[stored_chunk_to_document(r) for r in ChunkRepository(session).parent_chunks(scope=scope,expected={anchor['chunk_id']:anchor})]
                if len(documents)!=1 or documents[0].page_content!=anchor['content']:
                    raise ValueError('Frozen anchor changed')
                kb_names={str(k):n for k,n in scope.knowledge_base_names}
                documents[0].metadata['knowledge_base_name']=kb_names.get(documents[0].metadata['knowledge_base_id'])
                rendered=ContextBuilder().build(documents,question=case['question'])
                budget=rendered.total_characters-40
                protocol['probes'].append(dict(id=case['id'],chunk_id=anchor['chunk_id'],parent_id=anchor['parent_id'],
                    original_characters=len(anchor['content']),full_rendered_characters=rendered.total_characters,budget=budget))
                prepared.append((case,documents,budget))
            (out/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding='utf-8')

            class FrozenEvidence:
                def __init__(self,documents,retrieval):
                    self.documents,self.retrieval=documents,retrieval
                def collect(self,question,*,scope,session,**kwargs):
                    current=self.retrieval.refresh_evidence(self.documents,scope=scope,session=session)
                    if [(d.id,d.page_content) for d in current]!=[(d.id,d.page_content) for d in self.documents]:
                        raise ValueError('Frozen evidence changed')
                    record_documents('parent_context',current)
                    return CollectedEvidence(current)

            for index,(case,documents,budget) in enumerate(prepared):
                order=('full_parent','model_sections') if index%2==0 else ('model_sections','full_parent')
                for name in order:
                    variant=copy(settings);variant.rag_context_selection_enabled=name=='model_sections'
                    retrieval=RetrievalService(variant)
                    service=AnswerService(settings=variant,retrieval_service=retrieval,
                        context_builder=ContextBuilder(max_characters=budget,parent_selector=BudgetedParentSelector(variant)),
                        evidence_coordinator=FrozenEvidence(documents,retrieval))
                    tick=time.perf_counter()
                    with capture_trace() as trace,usage_context(scope=scope,session=session,settings=variant,
                        actor_user_id=principal.user_id,actor_external_subject=principal.external_subject):
                        result=service.answer(case['question'],scope=scope,session=session).model_dump(mode='json')
                    row=dict(trace.to_dict(),id=case['id'],variant=name,response=result,latency_ms=round((time.perf_counter()-tick)*1000,2))
                    folder=out/name;folder.mkdir(exist_ok=True)
                    with (folder/'traces.jsonl').open('a',encoding='utf-8') as stream:
                        stream.write(json.dumps(row,ensure_ascii=False)+'\n')
                    session.rollback()
                    print(json.dumps(dict(id=case['id'],variant=name,answerable=result['answerable'],latency_ms=row['latency_ms'],
                        selection_calls=sum(e.get('stage')=='context_selection' for e in row['stages'].get('model_usage',[])))),flush=True)
            current_readable=auth.list_readable_knowledge_base_ids(principal=principal)
            protocol.update(corpus_after=corpus_snapshot(session,principal,current_readable),finished_at=datetime.now(timezone.utc).isoformat())
            protocol['same_corpus']=protocol['corpus_before']==protocol['corpus_after']
            protocol['same_permissions']=readable==current_readable
            (out/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding='utf-8')
            return 0 if protocol['same_corpus'] and protocol['same_permissions'] else 2
    finally:
        engine.dispose()


if __name__=='__main__':
    raise SystemExit(main())
