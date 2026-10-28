"""Bounded real HTTP conversation checks with private, owner-scoped SQL diagnostics."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
from uuid import UUID

import requests
from dotenv import dotenv_values
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import Settings
from backend.app.db.conversation_models import ConversationTurn
from backend.app.db.model_usage_models import ModelUsageEvent
from backend.app.db.session import create_database_engine, create_session_factory
from backend.app.evaluation.runner import verified_principal
from backend.app.knowledge.conversation_memory import MAX_BYTES, encoded, valid_state
from backend.app.knowledge.conversations import ConversationStore
from backend.app.security.authorization import AuthorizationService
from scripts.run_load_test import remaining_token_seconds


def check_turn(spec, response, resolved, state, history, events):
    """Visible conditions are proxies; final answer truth still requires manual review."""
    checks = {}
    active = [i for i in state.items if i.status == 'active'] if state else []
    expected = spec.get('expect', {})
    for text in expected.get('rewrite_contains', []):
        checks['rewrite_has_' + text] = text in resolved
    for text in expected.get('rewrite_excludes', []):
        checks['rewrite_excludes_' + text] = text not in resolved
    for token in expected.get('window_excludes', []):
        checks['raw_window_excludes_' + token] = all(token not in t.question for t in history)
    for condition in expected.get('active', []):
        matches = [i for i in active if (not condition.get('field') or i.field == condition['field'])
                   and all(t in ' '.join([i.object, i.qualifier, i.value]) for t in condition['contains'])
                   and (condition.get('source_turn') is None or i.source.turn == condition['source_turn'])]
        checks['active_' + '_'.join(condition['contains'])] = bool(matches)
    for token in expected.get('active_excludes', []):
        checks['active_excludes_' + token] = all(token not in i.value for i in active)
    conflict_keys = {i.key for i in state.items if i.status == 'conflicted'} if state else set()
    if 'conflict' in expected:
        checks['conflict_expected'] = bool(conflict_keys) == expected['conflict']
    if 'clarification' in expected:
        reason = response.get('refusal_reason') or ''
        clarified = not response.get('answerable') and not response.get('citations') and ('冲突' in reason or '会话条件' in reason)
        checks['clarification_expected'] = clarified == expected['clarification']
    if 'answerable' in expected:
        checks['answerable_expected'] = response.get('answerable') == expected['answerable']
    for token in expected.get('answer_contains', []):
        checks['answer_has_' + token] = token in response.get('answer', '')
    if expected.get('no_rag_calls'):
        checks['no_rag_calls'] = all(e.stage == 'conversation_rewrite' for e in events)
    checks['rewrite_call_bound'] = sum(e.stage == 'conversation_rewrite' for e in events) <= 1
    if state:
        checks['state_bounds'] = len(encoded(state)) <= MAX_BYTES and len(state.items) <= 16 and len(conflict_keys) <= 4
        checks['state_complete'] = not state.incomplete
    return checks


def usage(events):
    return dict(call_count=len(events), stages=dict(Counter(e.stage for e in events)),
                known_tokens=sum(e.total_tokens or 0 for e in events),
                unknown_usage_calls=sum(e.total_tokens is None for e in events),
                statuses=dict(Counter(e.status for e in events)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=ROOT / 'evals/conversation_memory/live_cases.json')
    parser.add_argument('--config', type=Path, default=ROOT / 'evals/system_config.local.json')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, default=ROOT / '.env.evaluation')
    parser.add_argument('--allow-model-calls', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding='utf-8-sig'))
    count = sum(len(c['turns']) for c in cases)
    if not 1 <= count <= 64 or len(cases) > 4 or any(not 1 <= len(c['turns']) <= 20 for c in cases):
        raise ValueError('Conversation validation exceeds bounded workload')
    if args.dry_run:
        print(json.dumps(dict(cases=[c['id'] for c in cases], qa_turns=count, model_calls_started=False)))
        return
    if not args.allow_model_calls:
        raise ValueError('Use --allow-model-calls for the authorized real model run')
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    base_url = config['base_url'].rstrip('/')
    if base_url != 'http://127.0.0.1:8000':
        raise ValueError('This diagnostic runner is limited to the existing local API')
    token = (dotenv_values(args.token_file).get('ENTERPRISE_KB_ACCESS_TOKEN') or '').removeprefix('Bearer ').strip()
    if remaining_token_seconds(token) < 600:
        raise ValueError('JWT must have at least 600 seconds remaining')
    out = args.output_dir.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Use a new empty output directory to preserve original results')
    out.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    client = requests.Session()
    client.headers['Authorization'] = 'Bearer ' + token
    records, all_events = [], []
    report = dict(evaluation_type='real_http_conversation_with_private_sql_memory_diagnostics',
        case_file_sha256=hashlib.sha256(args.cases.read_bytes()).hexdigest(), records=records,
        model=settings.chat_model, raw_reports_private=True,
        limitations=['One run per scenario, not a statistical accuracy estimate.',
                     'Keyword/field checks require semantic review of rewritten questions and final answers.',
                     'No permission mutations, fixture documents or production rule changes.'])
    def save():
        report['usage'] = usage(all_events)
        (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        with factory() as session:
            principal = verified_principal(settings, session, token)
            expected_scope = {UUID(v) for v in config['knowledge_bases'].values()}
            if AuthorizationService(session).list_readable_knowledge_base_ids(principal=principal) != expected_scope:
                raise ValueError('Current identity does not match the four-KB evaluation fixture')
            scope = AuthorizationService(session).require_retrieval_scope(principal=principal)
        for case in cases:
            row = dict(id=case['id'], turns=[], cleanup='not_created')
            records.append(row)
            cid = None
            try:
                create = client.post(base_url + '/api/conversations', timeout=30)
                if create.status_code != 201:
                    raise RuntimeError('conversation_create_failed')
                cid = UUID(create.json()['id'])
                row['conversation_id'] = str(cid)
                for index, turn in enumerate(case['turns'], 1):
                    with factory() as session:
                        scope = AuthorizationService(session).require_retrieval_scope(principal=principal)
                        if scope.knowledge_base_ids != expected_scope:
                            raise PermissionError('scope_changed')
                        store = ConversationStore(session, principal, scope)
                        history = store.turns(cid, limit=6, for_context=True)
                        before = store.get(cid).memory_json
                        started = time.perf_counter()
                        response = client.post(base_url + '/api/qa', json=dict(conversation_id=str(cid), question=turn['question']), timeout=120)
                        elapsed = round((time.perf_counter() - started) * 1000, 2)
                        item = dict(id=turn['id'], index=index, status_code=response.status_code, latency_ms=elapsed)
                        row['turns'].append(item)
                        request_id = response.headers.get('X-Request-ID')
                        events = list(session.scalars(select(ModelUsageEvent).where(
                            ModelUsageEvent.tenant_id == principal.tenant_id,
                            ModelUsageEvent.actor_user_id == principal.user_id,
                            ModelUsageEvent.request_id == request_id))) if request_id else []
                        all_events.extend(events)
                        item['usage'] = usage(events)
                        if response.status_code != 200:
                            item['error_type'] = 'non_success_http_status'
                            save()
                            break
                        current_scope = AuthorizationService(session).require_retrieval_scope(principal=principal)
                        if current_scope.knowledge_base_ids != scope.knowledge_base_ids:
                            raise PermissionError('scope_changed_during_request')
                        saved = store.get(cid)
                        raw = saved.memory_json
                        state = valid_state(raw, principal, scope, saved.turn_count) if raw else None
                        if state and not {UUID(v) for v in state.dependency_ids} <= scope.knowledge_base_ids:
                            raise PermissionError('memory_scope_mismatch')
                        last = session.scalar(select(ConversationTurn).where(
                            ConversationTurn.tenant_id == principal.tenant_id,
                            ConversationTurn.conversation_id == cid, ConversationTurn.turn_index == index))
                        if last is None:
                            raise RuntimeError('paired_turn_not_committed')
                        body = response.json()
                        item.update(question=turn['question'], response=body, retrieval_question=last.retrieval_question,
                            memory_before=before, memory_after=raw,
                            raw_window_indices=[t.turn_index for t in history],
                            checks=check_turn(turn, body, last.retrieval_question, state, history, events))
                        item['passed'] = all(item['checks'].values())
                        save()
                        print(f"{case['id']} {index}/{len(case['turns'])}: HTTP200, answerable={body.get('answerable')}, checks={item['passed']}, model_calls={len(events)}", flush=True)
            except Exception as exc:
                row['error_type'] = type(exc).__name__  # Never output credential-bearing exception strings.
            finally:
                if cid is not None:
                    try:
                        deleted = client.delete(base_url + f'/api/conversations/{cid}', timeout=30)
                        if deleted.status_code == 204:
                            row['cleanup'] = 'deleted_owned_temporary_conversation_via_http'
                        else:
                            with factory() as session:
                                # Only the owner-checked ID created above; never existing user conversations.
                                ConversationStore(session, principal, scope).delete(cid)
                            row['cleanup'] = 'deleted_owned_temporary_conversation_via_sql_fallback'
                    except Exception as exc:
                        row['cleanup'] = 'failed'
                        row['cleanup_error_type'] = type(exc).__name__
                save()
        report['completed_turns'] = sum(len(r['turns']) for r in records)
        report['planned_turns'] = count
        report['status'] = 'completed' if (report['completed_turns'] == count
            and not any('error_type' in r for r in records)
            and all(t['status_code'] == 200 for r in records for t in r['turns'])) else 'incomplete'
        save()
    finally:
        client.close()
        engine.dispose()
    print(json.dumps(dict(status=report.get('status'), completed_turns=report.get('completed_turns'), usage=report['usage']), ensure_ascii=False))


if __name__ == '__main__':
    main()
