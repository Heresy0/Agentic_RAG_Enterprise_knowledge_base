"""Offline diagnosis of the closed selection-contract batch; never executes QA.

Reads existing traces and replays local pure compilation/validation functions.
Network connections and DNS are forbidden by a process audit hook. Gold facts
are used only for locating evidence in saved stages, never for model requests.
"""
import argparse
import base64
from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unicodedata
from unittest.mock import patch


def deny_network(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.connect_ex'):
        raise RuntimeError('Offline diagnosis forbids network and DNS.')


sys.addaudithook(deny_network)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.knowledge.answer_models import EvidenceFirstAnswerDraft
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.context_builder import BuiltContext, ContextItem
from backend.app.knowledge.output_contract import generation_schema, prepare_draft, validate_output
from backend.app.manufacturing.answer_context import (
    ManufacturingAnswerContext, manufacturing_answer_context, comparison_question,
    facts_only_question, requested_execution_fields, attach_business_context,
)
from backend.app.manufacturing.execution_contracts import context_from_payload
from backend.app.manufacturing.generation_models import precision_projection
from backend.app.manufacturing.generation_selection import selection_schema
from backend.app.manufacturing.source_statements import source_units
from backend.app.security.oidc import OidcTokenVerifier, TokenVerificationError
from scripts import accept_erpnext_development_v2 as identity_runner

NUMBERS = (1, 3, 6, 9, 15, 21, 25, 29, 31, 41, 42, 44, 59, 62, 69, 78, 80, 86)
RETRIEVAL_STAGES = ('vector', 'keyword', 'fusion', 'expanded_candidates',
                    'policy_candidates_raw', 'policy_candidates', 'rerank_model', 'rerank',
                    'parent_context', 'authorized_final', 'context')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normal(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def hits(rows, quote):
    target = normal(quote)
    return [dict(document_name=x.get('document_name'), citation_id=x.get('citation_id'),
                 chunk_id=x.get('chunk_id'), rank=x.get('rank'), model_rank=x.get('model_rank'),
                 final_rank=x.get('final_rank'), rerank_score=x.get('rerank_score')) for x in rows
            if target and target in normal(x.get('content', ''))]


def reconstruct(record, question):
    """Frozen source bindings only; this does not revalidate live authorization."""
    stages = record['final']['trace']['stages']
    snapshots = [e['response']['snapshot']['manufacturing'] for e in record['events']
                 if e['method'] == 'GET' and isinstance(e.get('response'), dict)
                 and e['response'].get('snapshot', {}).get('manufacturing')]
    active = None
    bindings = []
    if stages.get('manufacturing_business'):
        snapshot = snapshots[0]
        app = snapshot['applicability']
        bindings = app['documents']
        decision = SimpleNamespace(state=app['state'], selected_document_code=app['document_code'],
            selected_business_revision=app['business_revision'],
            scope=SimpleNamespace(manufacturing=SimpleNamespace(
                documents=[SimpleNamespace(**x) for x in bindings], required_evidence=(), step_mapping=None)))
        active = ManufacturingAnswerContext(
            context_from_payload(stages['manufacturing_business'][0]['business_context']), decision,
            facts_only=facts_only_question(question), comparison=comparison_question(question))
    allowed = {f.name for f in fields(ContextItem)}
    items = []
    for row in stages.get('context', []):
        meta = next((x for x in stages.get('authorized_final', [])
                     if x.get('document_name') == row['document_name']), {})
        binding = next((x for x in bindings if x['document_id'] == meta.get('document_id')), {})
        data = dict(citation_id=row['citation_id'], content=row['content'], source=row['document_name'],
            document_id=meta.get('document_id'), document_name=row['document_name'],
            section_path='frozen-trace', chunk_id=row.get('chunk_id'), rerank_score=None,
            business_revision=binding.get('business_revision'), document_role=binding.get('document_role'))
        data.update({k: v for k, v in row.items() if k in allowed})
        items.append(ContextItem(**data))
    text = '\n'.join(item.content for item in items)
    context = BuiltContext(text=text, items=items, total_characters=len(text),
                           business_items=active.items if active else ())
    return context, active


def replay(record, case):
    context, active = reconstruct(record, case['question'])
    stage = record['final']['trace']['stages']['answer_generation'][0]
    data = dict(answerable=True, response_type='answer', claims=stage['claims'],
                coverage=stage['coverage']['coverage'])
    with manufacturing_answer_context(active):
        draft = generation_schema(context).model_validate(data)
        def check(candidate):
            v = validate_output(candidate, validator=AnswerValidator(), context=context, question=case['question'])
            return dict(valid=v.valid, errors=v.errors)
        result = dict(original=check(draft), recorded_errors=stage['validation_errors'])
        assert result['original']['errors'] == stage['validation_errors'], case['id']
        n = int(case['id'][-3:])
        if n in (1, 3):
            # Remove only the redundant notice-summary claim, preserving all
            # other text/citations. Remap coverage indices, never rewrite facts.
            removed = 4
            candidate = draft.model_copy(deep=True)
            candidate.claims.pop(removed - 1)
            for row in candidate.coverage:
                row.claim_indices = [i - (i > removed) for i in row.claim_indices if i != removed]
            result['drop_only_redundant_notice_claim'] = check(candidate)
            result['remaining_claims'] = [x.model_dump() for x in candidate.claims]
        if n == 15:
            q = case['question']
            variants = [q, q.replace('登记', '时间'), q.replace('时间精度', '开工时间精度'),
                        q.replace('登记', '时间').replace('时间精度', '开工时间精度')]
            result['field_projection_probes'] = []
            for question in variants:
                projected = precision_projection(question, context)
                result['field_projection_probes'].append(dict(question=question,
                    requested_fields=list(requested_execution_fields(question)),
                    projection_fields=[c.business_field for c in projected.claims] if projected else None))
            sources = [x.citation_id for x in context.business_items
                       if x.field_name in ('operation_actual_started_at', 'operation_start_precision')]
            payload = dict(data, claims=[dict(source=ref) for ref in sources])
            selected = prepare_draft(selection_schema(context).model_validate(payload), context)
            result['correct_operation_sources'] = dict(check(selected),
                claims=[x.model_dump() for x in selected.claims],
                actual_model_selected_fields=['actual_started_at', 'actual_start_precision'])
        if n in (21, 25):
            rendered = attach_business_context(context, question=case['question'])
            capability = ('本来源没有该字段值及唯一工序代码' if n == 21 else '步骤名称不是已审核工序代码')
            outputs = [x.content for x in context.business_items] + [text for _, text in source_units(context).values()]
            result['boundary_contract_probe'] = dict(
                explanation_in_model_context=capability in rendered.text,
                explanation_in_selectable_source_text=any(capability in text for text in outputs),
                live_claim_properties=list(selection_schema(context).model_json_schema()['$defs']['ManufacturingSourceSelection']['properties']),
                compiler_additions='fixed applicability and existing bounded duration/execution verdicts only')
        if n == 86:
            candidate = draft.model_copy(deep=True)
            for claim in candidate.claims:
                claim.citations = [ref.strip('[]') for ref in claim.citations]
            result['only_strip_citation_brackets'] = check(candidate)
        return result


def identity_probes(out):
    import jwt
    verifier = OidcTokenVerifier(issuer='https://offline.invalid', audience='offline',
        jwks_url='https://offline.invalid/certs',
        jwks_client=SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key='unused')))
    results = []
    for error in (jwt.ImmatureSignatureError, jwt.ExpiredSignatureError, jwt.InvalidAudienceError, jwt.InvalidSignatureError):
        with patch('backend.app.security.oidc.jwt.decode', side_effect=error('synthetic offline probe')):
            try:
                verifier.verify('synthetic')
            except TokenVerificationError as exc:
                results.append(dict(inner_error=type(exc.__cause__).__name__, outer_error=type(exc).__name__, message=str(exc)))
    broken = OidcTokenVerifier(issuer='https://offline.invalid', audience='offline', jwks_url='https://offline.invalid/certs',
        jwks_client=SimpleNamespace(get_signing_key_from_jwt=lambda _: (_ for _ in ()).throw(jwt.PyJWKClientError('synthetic'))))
    try:
        broken.verify('synthetic')
    except TokenVerificationError as exc:
        results.append(dict(inner_error=type(exc.__cause__).__name__, outer_error=type(exc).__name__, message=str(exc)))
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    claims = dict(iss='https://offline.invalid', sub='synthetic', iat=int(now.timestamp()) + 6,
                  exp=int(now.timestamp()) + 3600)
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    path = out / 'synthetic-identity.json'
    path.write_text(json.dumps(dict(admin=dict(access_token='synthetic.' + payload + '.synthetic'))), encoding='utf-8')
    calls = []
    app = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(oidc_issuer=claims['iss']),
        oidc_token_verifier=SimpleNamespace(verify=lambda _: calls.append('verify'))))
    with patch.object(identity_runner, 'datetime', SimpleNamespace(now=lambda _: now)):
        try:
            identity_runner.token(path, 'admin', app)
        except ValueError as exc:
            future = dict(iat_ahead_seconds=6, observed_error=str(exc), verifier_called=bool(calls), network_calls=0)
    assert len({x['message'] for x in results}) == 1 and not calls
    return dict(common_error_wrapping=results, future_iat_guard=future,
        limitation='Synthetic mechanisms only; not a reconstruction of the failed real JWT or JWKS state.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve(); out = args.out.resolve()
    run.relative_to(ROOT / 'tmp'); out.relative_to(ROOT / 'tmp')
    assert run != out
    out.mkdir(exist_ok=False)
    gold = ROOT / 'evals/manufacturing_gold_100_v4'
    cases = {c['id']: c for c in map(json.loads, (gold / 'cases.jsonl').read_text(encoding='utf-8').splitlines())}
    sources = read(gold / 'sources.json')['sources']
    protected = [gold / name for name in ('cases.jsonl', 'sources.json', 'review_policy.json', 'manifest.json')]
    protected += list((ROOT / 'evals/manufacturing_gold_100_selection_live_v1/runs/20261113-r2').glob('*'))
    code = ['knowledge/answer_validation.py', 'knowledge/answer_coverage.py', 'knowledge/condition_requirements.py',
            'knowledge/answer_service.py', 'knowledge/answer_repair.py', 'knowledge/output_contract.py',
            'manufacturing/answer_context.py', 'manufacturing/generation_selection.py', 'manufacturing/generation_models.py',
            'manufacturing/source_statements.py', 'security/oidc.py', 'evaluation/trace.py',
            'knowledge/retrieval_service.py', 'knowledge/rerank_retriever.py', 'knowledge/context_builder.py',
            'knowledge/evidence_selection.py']
    protected += [ROOT / 'backend/app' / name for name in code]
    protected += [ROOT / 'scripts/accept_erpnext_development_v2.py', Path(__file__).resolve()]
    frozen_business_matches = {}
    for name in code:
        live = ROOT / 'backend/app' / name
        frozen = run / 'source/backend/app' / name
        assert live.read_bytes().replace(b'\r\n', b'\n') == frozen.read_bytes().replace(b'\r\n', b'\n')
        frozen_business_matches[name] = True
        protected.append(frozen)
    for n in NUMBERS:
        protected += [run / f'MFG-GOLD-{n:03}{suffix}' for suffix in ('-result.json', '-stderr.log')]
    before = {str(p.relative_to(ROOT)).replace('\\', '/'): digest(p) for p in protected}
    rows = []
    for n in NUMBERS:
        key = f'MFG-GOLD-{n:03}'
        record = read(run / (key + '-result.json')); case = cases[key]
        final = record['final']; stages = final['trace']['stages'] if final else {}
        evidence = []
        for entry in case['evidence']:
            if entry['kind'] != 'text':
                continue
            quote = entry['quote']; source = sources[entry['source_id']]
            unit = next(u for u in source['units'] if u['id'] == entry['unit_id'])
            evidence.append(dict(evidence_id=entry['id'], source_id=entry['source_id'], quote=quote,
                quote_in_frozen_source_unit=normal(quote) in normal(unit['text']),
                stage_hits={name: hits(stages.get(name, []), quote) for name in RETRIEVAL_STAGES}))
        generated = stages.get('answer_generation', [{}])[0]
        extra = {}
        if n == 42:
            quote = next(e['quote'] for e in case['evidence'] if e['id'] == 'E01')
            ranking = stages['rerank_model']
            extra['rerank_cutoff_probe'] = dict(
                ranked_candidates=len(ranking), retained_candidates=len(stages['rerank']),
                required_quote_at_top12=bool(hits(ranking[:12], quote)),
                required_quote_at_top13=bool(hits(ranking[:13], quote)),
                limitation='Saved-ranking counterfactual only; not a new retrieval/model run or a proposed hard-coded K.')
        if n in (6, 41, 42, 44, 59, 62, 69, 78, 80, 86):
            old_path = ROOT / 'tmp/manufacturing-gold-100-v4-live-20261113-r1' / (key + '-result.json')
            if old_path.exists():
                before[str(old_path.relative_to(ROOT)).replace('\\', '/')] = digest(old_path)
                old = read(old_path)
                old_stages = old['final']['trace']['stages'] if old['final'] else {}
                signature = lambda s: [(x['document_name'], x['citation_id'], hashlib.sha256(x['content'].encode()).hexdigest()) for x in s.get('context', [])]
                extra['old_v4_recorded_document_context_identical'] = signature(stages) == signature(old_stages)
        rows.append(dict(case_id=key, question=case['question'], request_events=len(record['events']),
            model_calls=sum(e['model_calls'] for e in record['events']), harness_error=record['harness_error'],
            sources_in_final_context=[dict(citation_id=x['citation_id'], document_name=x['document_name'],
                sha256=hashlib.sha256(x['content'].encode()).hexdigest()) for x in stages.get('context', [])],
            context_selection=stages.get('context_selection', []), evidence=evidence,
            first_claims=generated.get('claims'), completion_changed=generated.get('completion_changed'),
            coverage=generated.get('coverage'),
            repair_events=[e for e in stages.get('rag_routing', []) if e.get('kind') in ('repair', 'repair_validation')],
            offline_replay=replay(record, case) if final else None,
            stderr_bytes=(run / (key + '-stderr.log')).stat().st_size, **extra))
    identity = identity_probes(out)
    assert all(digest(ROOT / name) == value for name, value in before.items())
    result = dict(baseline='d0b6b53', runtime_source_revision=read(run/'setup-receipt.json')['source_revision'],
        model_calls=0, network_connections_allowed=False, api_calls=0, database_calls=0, container_operations=0,
        old_scores_recomputed=False, inputs_unchanged=True, input_sha256=before, cases=rows, identity_probes=identity,
        frozen_business_source_matches=frozen_business_matches,
        limits='Saved contexts and processed first drafts are not full provider HTTP requests or raw wire responses; live auth is not revalidated.')
    (out / 'evidence.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(cases=len(rows),local_replays=sum(x['offline_replay'] is not None for x in rows),
                         protected_inputs=len(before),inputs_unchanged=True,model_calls=0,network=False)))


if __name__ == '__main__':
    main()
