"""Paired live full-parent versus budgeted model-section comparison."""
import argparse
from copy import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FOCUS = ['XH-001','XH-002','XH-006','XH-008','XH-009','XH-011','XH-012','XH-013',
         'XH-017','XH-019','XH-020','XH-025','XH-030','XH-D-23','XH-D-24','XH-D-28',
         'XH-D-29','XH-D-30','MH-001','MH-004','MH-005','MH-007','MH-008',
         'FP-001','FP-002','FP-003','FP-004','FP-005','FP-006','FP-007','FP-008']
PRICE_SOURCE = 'https://help.aliyun.com/zh/model-studio/model-pricing'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'evals/system_config.local.json')
    parser.add_argument('--dataset-dir', type=Path, default=ROOT/'evals/current')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--case-ids', nargs='+')
    parser.add_argument('--max-characters', type=int, default=12000)
    parser.add_argument('--allow-model-calls', action='store_true')
    args = parser.parse_args(argv)
    if not args.allow_model_calls: parser.error('Explicit --allow-model-calls required')
    if not 200 <= args.max_characters <= 12000: parser.error('Budget must be between 200 and 12000')
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()): raise ValueError('Choose a new empty output directory')
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env.evaluation', override=True)
    from backend.app.config import Settings
    from backend.app.db.session import create_database_engine, create_session_factory
    from backend.app.evaluation.metrics import SCORER_VERSION, summarize_stages
    from backend.app.evaluation.quality_policy import apply_quality_policy, EVALUATOR_VERSION
    from backend.app.evaluation.scoring_rules import apply_scoring_rules
    from backend.app.evaluation.runner import verified_principal, token_from_environment, score_trace
    from backend.app.evaluation.reporting import quality_layers, write_report
    from backend.app.evaluation.trace import capture_trace
    from backend.app.knowledge.answer_service import AnswerService
    from backend.app.knowledge.context_builder import ContextBuilder
    from backend.app.knowledge.parent_context_selection import BudgetedParentSelector, POLICY_VERSION
    from backend.app.knowledge.retrieval_service import RetrievalService
    from backend.app.model_usage import usage_context, parse_prices
    from backend.app.security.authorization import AuthorizationService
    from scripts.evaluate_multihop_comparison import corpus_snapshot, runtime
    from scripts.evaluate_answers import load_dataset
    cases = apply_scoring_rules(load_dataset(args.dataset_dir/'single_turn.jsonl'), args.dataset_dir/'scoring_rules.json')
    cases, policy_sha = apply_quality_policy(cases, args.dataset_dir/'quality_policy.json', args.dataset_dir/'single_turn.jsonl')
    if args.case_ids:
        if set(args.case_ids)-{c['id'] for c in cases}: raise ValueError('Unknown selected ID')
        cases = [c for c in cases if c['id'] in args.case_ids]
    # Freeze all original references and explicit evaluation criteria before calls.
    (out/'frozen-criteria.json').write_text(json.dumps(dict(cases=cases, condition_focus_ids=FOCUS,
        instructions='Review all necessary conditions/exceptions against reference and original evidence; distinguish context omission, answer omission and refusal. Do not treat keyword proxy as semantic accuracy.'),
        ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    settings = Settings()
    if (settings.chat_model,settings.embedding_model,settings.rerank_model)!=(
            'qwen3.7-flash','text-embedding-v4','qwen3-rerank'):
        raise ValueError('This frozen public price table requires the evaluated Qwen model set')
    prices = {f'dashscope:{settings.chat_model}':dict(version='aliyun-public-20261022',currency='CNY',input_per_million='0.2',output_per_million='0.8',cached_input_per_million='0.04'),
              f'dashscope:{settings.embedding_model}':dict(version='aliyun-public-20261022',currency='CNY',total_per_million='0.5'),
              f'dashscope:{settings.rerank_model}':dict(version='aliyun-public-20261022',currency='CNY',total_per_million='0.5')}
    settings.model_usage_prices = parse_prices(json.dumps(prices))
    variants = {}
    for name, enabled in [('full_parent',False),('model_sections',True)]:
        item = copy(settings); item.rag_context_selection_enabled = enabled
        variants[name] = item
    protocol = dict(started_at=datetime.now(timezone.utc).isoformat(),git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        kind='authenticated_paired_live_single_pass',case_ids=[c['id'] for c in cases],repetitions=1,
        dataset_sha256=sha(args.dataset_dir/'single_turn.jsonl'),frozen_criteria_sha256=sha(out/'frozen-criteria.json'),
        quality_policy_sha256=policy_sha,scorer_version=SCORER_VERSION,evaluator_version=EVALUATOR_VERSION,
        parent_policy_version=POLICY_VERSION,context_max_characters=args.max_characters,
        code_sha256={p.relative_to(ROOT).as_posix():sha(p) for folder in ('backend/app','scripts') for p in (ROOT/folder).rglob('*.py') if '__pycache__' not in p.parts},
        variants={name:dict(runtime(v),rag_context_selection_enabled=v.rag_context_selection_enabled,
                           rag_context_selection_output_tokens=v.rag_context_selection_output_tokens,
                           rag_context_selection_prompt_bytes=v.rag_context_selection_prompt_bytes,
                           rag_context_selection_timeout_seconds=v.rag_context_selection_timeout_seconds,
                           context_max_characters=args.max_characters) for name,v in variants.items()},
        order='alternate full-first and model-first per case',business_permission_changes=False,
        prices=prices,price_source=PRICE_SOURCE,price_interpretation='Public Beijing real-time rates, <=32K chat input tier, cached-input discount. Estimates exclude free quota/contracts; verify input tier after run.',
        timing='backend pipeline + usage accounting; excludes initial authentication/client construction; not HTTP latency',
        semantic_review='Pending separate assistant review, not human or independent held-out benchmark')
    results = {name:[] for name in variants}
    streams = {}
    engine = create_database_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as session:
            profile = config['profiles']['全范围测试用户']
            principal = verified_principal(settings,session,token_from_environment(profile['token_env']))
            authorization = AuthorizationService(session)
            readable = authorization.list_readable_knowledge_base_ids(principal=principal)
            kb_map = {key:UUID(value) for key,value in config['knowledge_bases'].items()}
            if readable != {kb_map[g] for g in profile['expected_groups']}: raise ValueError('Effective scope differs')
            protocol['corpus_before'] = corpus_snapshot(session,principal,readable)
            (out/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            services = {name:AnswerService(settings=v,retrieval_service=RetrievalService(v),
                context_builder=ContextBuilder(max_characters=args.max_characters,parent_selector=BudgetedParentSelector(v))) for name,v in variants.items()}
            for name in variants:
                folder=out/name;folder.mkdir();streams[name]=(folder/'traces.jsonl').open('x',encoding='utf-8')
            consecutive_errors=0
            for index,case in enumerate(cases):
                order=('full_parent','model_sections') if index%2==0 else ('model_sections','full_parent')
                pair_scopes=[]
                for name in order:
                    trace=None;tick=time.perf_counter()
                    try:
                        scope=authorization.require_retrieval_scope(principal=principal,knowledge_base_id=kb_map.get(case.get('expected_scope')))
                        if not {kb_map[g] for g in case.get('source_groups',[])}.issubset(scope.knowledge_base_ids): raise ValueError('Required scope missing')
                        with capture_trace() as trace, usage_context(scope=scope,session=session,settings=variants[name],
                            actor_user_id=principal.user_id,actor_external_subject=principal.external_subject):
                            response=services[name].answer(case['question'],scope=scope,session=session).model_dump(mode='json')
                        snapshot=dict(trace.to_dict(),id=case['id'],variant=name,schema_version=1,origin='jwt_verified_local_backend_services',
                            question_hash=hashlib.sha256(case['question'].encode()).hexdigest(),response=response,latency_ms=round((time.perf_counter()-tick)*1000,2))
                        consecutive_errors=0
                    except Exception as exc:
                        snapshot=dict(trace.to_dict() if trace else {},id=case['id'],variant=name,schema_version=1,
                            question_hash=hashlib.sha256(case['question'].encode()).hexdigest(),error_type=type(exc).__name__,latency_ms=round((time.perf_counter()-tick)*1000,2))
                        consecutive_errors+=1
                    finally:session.rollback()
                    streams[name].write(json.dumps(snapshot,ensure_ascii=False)+'\n');streams[name].flush()
                    record=score_trace(case,snapshot,include_answer=True);results[name].append(record)
                    pair_scopes.append(snapshot.get('scope'))
                    print(json.dumps(dict(case=case['id'],variant=name,status=record['status'],answerable=(snapshot.get('response') or {}).get('answerable'),
                        selection_calls=sum(e.get('stage')=='context_selection' for e in snapshot.get('stages',{}).get('model_usage',[])),latency_ms=snapshot['latency_ms']),ensure_ascii=False),flush=True)
                    if consecutive_errors>=3: raise RuntimeError('Stopped after three consecutive infrastructure errors; no automatic retry')
                if any(scope!=pair_scopes[0] for scope in pair_scopes[1:]):raise ValueError('Paired authorization scopes differ')
            now_readable=authorization.list_readable_knowledge_base_ids(principal=principal)
            protocol['corpus_after']=corpus_snapshot(session,principal,now_readable)
            protocol['same_corpus']=protocol['corpus_before']==protocol['corpus_after']
            protocol['same_permissions']=readable==now_readable
            protocol['finished_at']=datetime.now(timezone.utc).isoformat()
            session.rollback()
        for name,records in results.items():
            quality=dict(records=records,stages=summarize_stages(records),evaluation_type='authenticated_paired_local_backend')
            report=dict(schema_version=1,mode='live',selected_cases=len(cases),selected_case_ids=[c['id'] for c in cases],
                dataset_sha256=protocol['dataset_sha256'],quality_policy_sha256=policy_sha,evaluator_version=EVALUATOR_VERSION,
                scorer_version=SCORER_VERSION,runtime=protocol['variants'][name],variant=name,layers=quality_layers(quality),protocol_path='../protocol.json')
            write_report(report,out/name)
        (out/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(status='completed',same_corpus=protocol['same_corpus'],same_permissions=protocol['same_permissions'],output=str(out))),flush=True)
        return 0 if protocol['same_corpus'] and protocol['same_permissions'] else 2
    finally:
        for stream in streams.values():stream.close()
        engine.dispose()


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print(json.dumps(dict(status='comparison_error',error_type=type(exc).__name__)),flush=True)
        raise SystemExit(3)
