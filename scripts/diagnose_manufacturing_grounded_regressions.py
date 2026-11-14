"""Offline comparison of the ten r2 -> r5 regressions; no QA or model calls."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# This helper installs a process-wide connection/DNS denial hook before imports.
from scripts.diagnose_manufacturing_selection_failures import (
    read, digest, reconstruct, hits, manufacturing_answer_context,
)
from backend.app.knowledge.answer_models import EvidenceFirstAnswerDraft
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.answer_coverage import requirement_prompt
from backend.app.knowledge.answer_repair import REPAIR_PROMPT
from backend.app.knowledge.output_contract import validate_output, prepare_draft, with_manufacturing_rules
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.manufacturing.generation_selection import GroundedDraft
from backend.app.manufacturing.source_statements import source_units
from langchain_core.documents import Document

NUMBERS = (24, 36, 50, 57, 60, 68, 77, 91, 94, 95)
OLD = ROOT / 'tmp/manufacturing-gold-100-v4-selection-live-20261113-r2'
NEW = ROOT / 'tmp/manufacturing-gold-100-v4-grounded-live-20261113-r5'
GOLD = ROOT / 'evals/manufacturing_gold_100_v4'


def qa(record):
    return next((e for e in record['events'] if e['method'] == 'POST'
                 and e['path'] in ('/api/qa', '/api/manufacturing/qa')), None)


def signature(rows):
    return [(r.get('chunk_id'), r.get('document_name'),
             hashlib.sha256(r.get('content', '').encode()).hexdigest()) for r in rows]


def describe(record):
    event = qa(record)
    stages = event['trace']['stages'] if event else {}
    gen = stages.get('answer_generation', [{}])[0]
    selection = stages.get('context_selection', [{}])[0]
    return dict(route=event['path'] if event else None, harness_error=record['harness_error'],
        events=[dict(method=e['method'], path=e['path'], status=e['status_code'], model_calls=e['model_calls'])
                for e in record['events']],
        manufacturing_context=bool(stages.get('manufacturing_business')),
        context_count=len(stages.get('context', [])), context_characters=selection.get('selected_characters'),
        context_status=stages.get('context_status', []),
        context_selection={k:v for k,v in selection.items() if k != 'parent_selections'},
        context_documents=[dict(citation_id=r['citation_id'], document_name=r['document_name'],
            characters=len(r['content']), content_sha256=hashlib.sha256(r['content'].encode()).hexdigest())
            for r in stages.get('context', [])],
        first_claims=gen.get('claims'), coverage=gen.get('coverage'), validation_errors=gen.get('validation_errors'),
        routing=[r for r in stages.get('rag_routing', []) if r.get('kind') != 'manufacturing_binding_compilation'],
        model_stages=[dict(stage=x['stage'], status=x['status'], error_type=x.get('details', {}).get('error_type'))
                      for x in stages.get('model_usage', [])])


def draft_for(record, case):
    context, active = reconstruct(record, case['question'])
    gen = qa(record)['trace']['stages']['answer_generation'][0]
    cls = GroundedDraft if active else EvidenceFirstAnswerDraft
    draft = cls.model_validate(dict(answerable=True, response_type='answer', claims=gen['claims'],
                                   coverage=gen['coverage']['coverage']))
    return context, active, draft, gen


def check(draft, context, question):
    result = validate_output(draft, validator=AnswerValidator(), context=context, question=question)
    return dict(valid=result.valid, errors=result.errors)


def repair_bytes(draft, context, question, errors):
    # Same serialization/schema calculation as the frozen AnswerRepair, no invocation.
    data = dict(question=question, requirements=json.loads(requirement_prompt(question, context)),
        evidence=[dict(citation_id=x.citation_id, content=x.content) for x in context.items],
        draft=draft.model_dump(), errors=errors)
    return (len(with_manufacturing_rules(REPAIR_PROMPT, context, draft=draft).encode())
            + len(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode())
            + len(json.dumps(type(draft).model_json_schema()).encode()) + 256)


def local_probe(old, new, case):
    number = int(case['id'][-3:]); question = case['question']
    if not qa(new):
        return dict(no_main_post=True, stderr_bytes=(NEW/(case['id']+'-stderr.log')).stat().st_size,
            limitation='No saved JWT/JWKS inner exception; cannot determine expiry, clock or key cause.')
    stages = qa(new)['trace']['stages']
    if number == 95:
        # Source metadata is incomplete in traces. This is a mechanism probe,
        # not an exact reconstruction of the original formatted prompt length.
        docs = [Document(id=r['chunk_id'], page_content=r['content'],
            metadata={k:v for k,v in r.items() if k not in ('content', 'rank')})
            for r in stages['authorized_final']]
        all_context = ContextBuilder().build(docs, question=question)
        first = ContextBuilder().build(docs[:1], question=question)
        assert all_context.incomplete_reason == 'parent_context_budget_incomplete'
        assert first.incomplete_reason is None
        return dict(all_saved_parents_reason=all_context.incomplete_reason,
            first_parent_only_reason=first.incomplete_reason, first_parent_characters=len(docs[0].page_content),
            answer_generation_calls=sum(x['stage']=='answer_generation' for x in stages['model_usage']),
            pre_generation_decisions=stages.get('answer_decisions'),
            model_context_selection=stages.get('context_selection', [{}])[0].get('parent_selections'),
            limitation='No parent selector/model is invoked; metadata missing from traces prevents exact byte/character replay. Keeping only one parent is a diagnostic counterfactual, not a proposed production rule.')
    context, active, draft, gen = draft_for(new, case)
    with manufacturing_answer_context(active):
        result = dict(original=check(draft, context, question))
        assert result['original']['errors'] == gen['validation_errors'], case['id']
        if number == 36:
            start = next(x for x in context.business_items if x.field_name == 'job_card_actual_started_at')
            result['start_source_present'] = dict(citation=start.citation_id, field=start.field_name)
            result['claim_kinds'] = [x.claim_kind for x in draft.claims]
            cited = draft.model_copy(deep=True); cited.claims[0].citations.append(start.citation_id)
            result['only_add_correct_start_citation'] = check(cited, context, question)
            unit, text = next((item, text) for item, text in source_units(context).values()
                              if text.startswith('首件确认须连续检查3件'))
            exact = draft.model_copy(deep=True)
            exact.claims[1].text = text; exact.claims[1].citations = [unit.citation_id]
            exact = prepare_draft(exact, context)
            assert exact.claims[1].claim_kind == 'work_requirement'
            result['replace_only_second_claim_with_cited_original'] = check(exact, context, question)
            result['exact_source_text'] = text
            exact.claims[1].claim_kind = None
            result['same_exact_text_only_remove_internal_role'] = check(exact, context, question)
            assert result['replace_only_second_claim_with_cited_original']['valid']
            assert not result['same_exact_text_only_remove_internal_role']['valid']
            result['limitation'] = 'Proves legacy role/exact-text coupling, not semantic approval of the unchanged other claims.'
        if number == 91:
            remap = draft.model_copy(deep=True); remap.coverage[1].claim_indices = [2]
            result['only_change_second_coverage_index_3_to_2'] = check(remap, context, question)
            assert result['only_change_second_coverage_index_3_to_2']['valid']
            old_context, _ = reconstruct(old, question)
            errors = gen['validation_errors']
            result['repair_prompt_bytes_new_context'] = repair_bytes(draft, context, question, errors)
            result['repair_prompt_bytes_old_context_same_new_draft'] = repair_bytes(draft, old_context, question, errors)
            result['repair_prompt_bytes_first_five_same_new_draft'] = repair_bytes(draft, replace(context, items=context.items[:5]), question, errors)
            result['frozen_default_limit_bytes'] = 24000
            assert result['repair_prompt_bytes_new_context'] > 24000
            assert result['repair_prompt_bytes_first_five_same_new_draft'] < 24000
            result['limitation'] = 'Proves budget eligibility difference only; no claim that a hypothetical model repair would succeed.'
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    out = parser.parse_args().out.resolve(); out.relative_to(ROOT/'tmp'); out.mkdir(exist_ok=False)
    cases = {c['id']:c for c in map(json.loads, (GOLD/'cases.jsonl').read_text(encoding='utf-8').splitlines())}
    protected = [GOLD/name for name in ('cases.jsonl', 'sources.json', 'review_policy.json', 'manifest.json')]
    for run in (OLD, NEW):
        protected += [run/(f'MFG-GOLD-{n:03}'+suffix) for n in NUMBERS for suffix in ('-result.json','-stderr.log','-stdout.log')]
        protected += list((ROOT/'evals'/('manufacturing_gold_100_selection_live_v1' if run==OLD else 'manufacturing_gold_100_grounded_live_v1')/'runs'/('20261113-r2' if run==OLD else '20261113-r5')).glob('*'))
    code = ['knowledge/answer_validation.py','knowledge/answer_repair.py','knowledge/answer_coverage.py',
        'knowledge/context_builder.py','knowledge/output_contract.py','manufacturing/source_statements.py',
        'manufacturing/generation_models.py','manufacturing/generation_selection.py','knowledge/retrieval_service.py',
        'knowledge/rerank_retriever.py','security/oidc.py','config.py']
    for name in code:
        current = ROOT/'backend/app'/name; frozen = NEW/'source/backend/app'/name
        assert current.read_bytes().replace(b'\r\n',b'\n') == frozen.read_bytes().replace(b'\r\n',b'\n'), name
        protected += [current, frozen]
    protected += [Path(__file__).resolve(), ROOT/'scripts/diagnose_manufacturing_selection_failures.py']
    auth_unchanged = {}
    for name in ('backend/app/security/oidc.py','scripts/accept_erpnext_development_v2.py'):
        old_file=OLD/'source'/name; new_file=NEW/'source'/name
        protected += [old_file,new_file]
        auth_unchanged[name] = old_file.read_bytes().replace(b'\r\n',b'\n') == new_file.read_bytes().replace(b'\r\n',b'\n')
        assert auth_unchanged[name]
    before = {str(p.relative_to(ROOT)).replace('\\','/'):digest(p) for p in protected}
    rows = []
    for n in NUMBERS:
        key = f'MFG-GOLD-{n:03}'; old=read(OLD/(key+'-result.json')); new=read(NEW/(key+'-result.json'))
        old_stages=qa(old)['trace']['stages'] if qa(old) else {}
        new_stages=qa(new)['trace']['stages'] if qa(new) else {}
        a=signature(old_stages.get('context',[])); b=signature(new_stages.get('context',[]))
        rows.append(dict(case_id=key,question=cases[key]['question'],old=describe(old),new=describe(new),
            ordered_child_inputs_equal={k:signature(old_stages.get(k,[]))==signature(new_stages.get(k,[]))
                for k in ('fusion','policy_candidates','rerank_model')}
                if all(old_stages.get(k) and new_stages.get(k) for k in ('fusion','policy_candidates','rerank_model')) else None,
            old_contexts_retained_in_new=len(set(a)&set(b)),equal_positions=sum(x==y for x,y in zip(a,b)),
            source_evidence=[dict(evidence_id=e['id'],quote=e['quote'],old_context_hits=hits(old_stages.get('context',[]),e['quote']),
                new_context_hits=hits(new_stages.get('context',[]),e['quote'])) for e in cases[key]['evidence'] if e['kind']=='text'],
            local_probe=local_probe(old,new,cases[key])))
    assert all(digest(ROOT/name)==value for name,value in before.items())
    result=dict(schema_version='manufacturing-grounded-regression10-diagnosis-v1',old_source='5947aa1',new_source='4c9663b',
        model_calls=0,api_calls=0,database_calls=0,container_operations=0,network_connections_allowed=False,
        inputs_unchanged=True,input_sha256=before,frozen_replay_code_matches=code,auth_runtime_code_unchanged=auth_unchanged,
        old_scores_recomputed=False,cases=rows,
        limits='Saved context and processed draft only; not complete supplier HTTP requests. No live permission/source checks. Offline validity probes do not create new semantic scores or prove stochastic generation causality.')
    with (out/'evidence.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(cases=len(rows),protected_inputs=len(before),unchanged=True,model_calls=0,network=False)))


if __name__=='__main__':
    main()
