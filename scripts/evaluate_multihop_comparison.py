"""Paired authenticated comparison against the project's single-pass RAG ablation.

Uses the same sources, prompt and validation. The default paired comparison
shares parent context. Parent ablation compares ordinary child-only RAG,
current child-only RAG and current RAG with parent context in one fresh run.
"""
import argparse
from copy import copy
import hashlib
from itertools import permutations
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def baseline_settings(settings):
    baseline = copy(settings)
    baseline.agentic_rag_mode = 'off'
    baseline.rag_planner_mode = 'off'
    baseline.rag_repair_enabled = False
    baseline.rag_feedback_enabled = False
    return baseline


def paired_order(index):
    return ('current', 'ordinary') if index % 2 == 0 else ('ordinary', 'current')


def comparison_settings(settings, comparison):
    if comparison == 'paired':
        return {'current': settings, 'ordinary': baseline_settings(settings)}
    if comparison != 'parent-ablation':
        raise ValueError('Unknown comparison')
    child = copy(settings)
    child.rag_parent_context_enabled = False
    parent = copy(settings)
    parent.rag_parent_context_enabled = True
    return {'ordinary_child': baseline_settings(child), 'current_child': child,
            'current_parent': parent}


def comparison_order(index, names):
    orders = tuple(permutations(names))
    return orders[index % len(orders)]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def runtime(settings):
    keys = ('agentic_rag_mode', 'rag_planner_mode', 'rag_repair_enabled', 'rag_feedback_enabled',
            'rag_parent_context_enabled', 'rag_rerank_top_k', 'chat_model', 'embedding_model',
            'rerank_model', 'rag_max_optional_calls', 'rag_max_optional_tokens',
            'rag_max_optional_seconds', 'rag_max_searches')
    result = {key: getattr(settings, key) for key in keys}
    result.update(temperature=0, reasoning_effort='none', max_retries=0,
                  context_max_characters=12000, context_max_chunks_per_source=4)
    from urllib.parse import urlsplit
    result['model_destinations'] = {key: urlsplit(getattr(settings, key)).scheme + '://' +
        urlsplit(getattr(settings, key)).netloc + urlsplit(getattr(settings, key)).path
        for key in ('chat_base_url', 'rerank_url')}
    return result


def corpus_snapshot(session, principal, readable_ids):
    from sqlalchemy import select
    from backend.app.db.models import KnowledgeDocument, DocumentChunk, DocumentParent
    docs = session.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.tenant_id == principal.tenant_id,
        KnowledgeDocument.knowledge_base_id.in_(readable_ids))).all()
    doc_ids = [doc.id for doc in docs]
    details = {'documents': sorted((str(d.id), str(d.knowledge_base_id), d.file_name,
                                   d.status, d.content_hash) for d in docs)}
    for name, model in (('chunks', DocumentChunk), ('parents', DocumentParent)):
        rows = session.scalars(select(model).where(model.tenant_id == principal.tenant_id,
                                                   model.document_id.in_(doc_ids))).all()
        details[name] = sorted((str(row.id), str(row.document_id), row.vector_generation,
                               hashlib.sha256(row.content.encode()).hexdigest()) for row in rows)
    return dict(sha256=fingerprint(details), counts={key: len(rows) for key, rows in details.items()},
                documents=details['documents'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--dataset-dir', type=Path, default=ROOT/'evals/current/groups/multihop')
    parser.add_argument('--corpus', type=Path, default=ROOT/'output/knowledge-corpus-100')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--allow-model-calls', action='store_true')
    parser.add_argument('--comparison', choices=('paired', 'parent-ablation'), default='paired')
    args = parser.parse_args(argv)
    if not args.allow_model_calls:
        parser.error('Explicit --allow-model-calls is required')
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError('Choose a new empty comparison directory')
    from uuid import UUID
    from backend.app.config import Settings
    from backend.app.db.session import create_database_engine, create_session_factory
    from backend.app.security.authorization import AuthorizationService
    from backend.app.evaluation.runner import verified_principal, token_from_environment, evaluate_local, score_trace
    from backend.app.evaluation.metrics import SCORER_VERSION, summarize_stages, summarize_records
    from backend.app.evaluation.reporting import quality_layers, write_report
    from backend.app.evaluation.trace import capture_trace
    from backend.app.knowledge.retrieval_service import RetrievalService
    from backend.app.knowledge.answer_service import AnswerService
    from backend.app.model_usage import usage_context
    cases = [json.loads(line) for line in (args.dataset_dir/'single_turn.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if len(cases) != 12 or len({c['id'] for c in cases}) != 12 or sum(c['answerable'] for c in cases) != 9:
        raise ValueError('Expected existing12 cases,9 positive and3 negative')
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    manifest = json.loads((args.corpus/'manifest.json').read_text(encoding='utf-8'))
    # No model calls: verify all manifest sources are currently ready and indexed.
    audit = evaluate_local([], config, include_answer=False, include_index=True, expected_documents=manifest['documents'])
    (out/'index-audit.json').write_text(json.dumps(audit['index'], ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if any(row['status'] != 'passed' for row in audit['index']['records']):
        raise ValueError('Corpus index audit failed; no paired model calls made')
    settings = Settings()
    variants = comparison_settings(settings, args.comparison)
    models = ('chat_model', 'embedding_model', 'rerank_model', 'chat_base_url', 'rerank_url',
              'chat_api_key', 'rag_rerank_top_k')
    if any(getattr(settings, key) != getattr(value, key) for value in variants.values() for key in models):
        raise ValueError('Shared retrieval/model settings mismatch')
    kb_map = {key: UUID(value) for key, value in config['knowledge_bases'].items()}
    profile = config['profiles']['全范围测试用户']
    engine = create_database_engine(settings.database_url)
    results = {name: [] for name in variants}
    streams = {}
    protocol = dict(dataset_sha256=hashlib.sha256((args.dataset_dir/'single_turn.jsonl').read_bytes()).hexdigest(),
        corpus_manifest_sha256=hashlib.sha256((args.corpus/'manifest.json').read_bytes()).hexdigest(),
        scope_config_sha256=fingerprint({k: config.get(k) for k in ('base_url', 'knowledge_bases', 'profiles')}),
        scorer_version=SCORER_VERSION, scoring_rules_sha256=None,
        variants={name: runtime(value) for name, value in variants.items()},
        comparison=args.comparison,
        order=[dict(id=case['id'], variants=comparison_order(index, tuple(variants))) for index, case in enumerate(cases)],
        baseline=('Project single-pass ablation, retaining shared parent context, prompt, deterministic supplements and final safety validation.'
                  if args.comparison == 'paired' else
                  'Three-arm ablation: ordinary_child disables optional multistep controls and parent context; current_child disables parent context only; current_parent enables parent context. Same child retrieval settings, prompt, deterministic supplements and final validation. Agent child-neighbor reads remain available in current_child.'),
        timing='latency_ms: backend scope check + answer pipeline and usage context; excludes initial authentication, client construction and index audit; no HTTP gateway.',
        repetitions_per_variant=1, warmup_model_calls=0, business_permission_changes=False)
    try:
        for name in variants:
            folder = out/name
            folder.mkdir()
            streams[name] = (folder/'traces.jsonl').open('x', encoding='utf-8')
        with create_session_factory(engine)() as session:
            principal = verified_principal(settings, session, token_from_environment(profile['token_env']))
            authorization = AuthorizationService(session)
            readable = authorization.list_readable_knowledge_base_ids(principal=principal)
            if readable != {kb_map[group] for group in profile['expected_groups']}:
                raise ValueError('Authenticated effective scope differs from configured profile')
            protocol['corpus_before'] = corpus_snapshot(session, principal, readable)
            services = {name: AnswerService(settings=value, retrieval_service=RetrievalService(value)) for name, value in variants.items()}
            (out/'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            for index, case in enumerate(cases):
                pair_scopes = []
                for name in comparison_order(index, tuple(variants)):
                    trace = None
                    started = time.perf_counter()
                    try:
                        scope = authorization.require_retrieval_scope(principal=principal, knowledge_base_id=kb_map.get(case['expected_scope']))
                        if not {kb_map[group] for group in case['source_groups']}.issubset(scope.knowledge_base_ids):
                            raise ValueError('Required evidence scope missing')
                        with capture_trace() as trace, usage_context(scope=scope, session=session, settings=variants[name],
                                actor_user_id=principal.user_id, actor_external_subject=principal.external_subject):
                            response = services[name].answer(case['question'], scope=scope, session=session).model_dump(mode='json')
                        snapshot = dict(trace.to_dict(), id=case['id'], schema_version=1,
                            origin='jwt_verified_local_backend_services', variant=name,
                            question_hash=hashlib.sha256(case['question'].encode()).hexdigest(),
                            latency_ms=round((time.perf_counter()-started)*1000, 2), response=response)
                    except Exception as exc:
                        snapshot = dict(trace.to_dict() if trace else {}, id=case['id'], schema_version=1,
                            variant=name, error_type=type(exc).__name__, latency_ms=round((time.perf_counter()-started)*1000, 2))
                    finally:
                        session.rollback()
                    streams[name].write(json.dumps(snapshot, ensure_ascii=False)+'\n')
                    streams[name].flush()
                    results[name].append(score_trace(case, snapshot, include_answer=True))
                    pair_scopes.append(snapshot.get('scope'))
                    print(json.dumps(dict(case=case['id'], variant=name, status=results[name][-1]['status']), ensure_ascii=False), flush=True)
                if any(scope != pair_scopes[0] for scope in pair_scopes[1:]):
                    raise ValueError('Paired authorization scopes differ')
            current_readable = authorization.list_readable_knowledge_base_ids(principal=principal)
            protocol['corpus_after'] = corpus_snapshot(session, principal, current_readable)
            protocol['same_corpus_before_after'] = protocol['corpus_before'] == protocol['corpus_after']
            protocol['same_effective_permissions'] = readable == current_readable
            session.rollback()
        (out/'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        for name, records in results.items():
            quality = dict(records=records, stages=summarize_stages(records), evaluation_type='authenticated_paired_local_backend')
            report = dict(schema_version=1, mode='live', selected_cases=12, available_cases=12,
                selected_case_ids=[c['id'] for c in cases], dataset_sha256=protocol['dataset_sha256'],
                corpus_manifest_sha256=protocol['corpus_manifest_sha256'], scope_config_sha256=protocol['scope_config_sha256'],
                scorer_version=SCORER_VERSION, scoring_rules_sha256=None, runtime=protocol['variants'][name],
                variant=name, layers=quality_layers(quality), case_outcomes=summarize_records(records),
                protocol_path='../protocol.json', limitations=['Single paired run on12 synthetic regression cases; not an independent benchmark.'])
            # Keep authoritative scope/error failures even if a proxy metric passed.
            for record, answer_record in zip(records, report['layers']['answer']['records']):
                if record['status'] in ('error', 'failed'):
                    answer_record['status'] = record['status']
            report['layers']['answer']['summary'] = summarize_records(report['layers']['answer']['records'])
            write_report(report, out/name)
        return 0 if protocol['same_corpus_before_after'] and protocol['same_effective_permissions'] else 2
    finally:
        for stream in streams.values():
            stream.close()
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
