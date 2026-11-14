"""Read-only diagnosis of r8 regressions against r5; no model or service calls.

Reuses the existing process-wide connection/DNS denial hook. Replay uses saved
processed drafts and source bindings, not complete supplier request payloads.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.diagnose_manufacturing_selection_failures import (
    read, digest, reconstruct, hits, manufacturing_answer_context,
)
from backend.app.knowledge.answer_models import EvidenceFirstAnswerDraft
from backend.app.knowledge.answer_validation import AnswerValidator, identifiers, without_identifiers
from backend.app.knowledge.output_contract import validate_output
from backend.app.knowledge.answer_repair import numeric_binding_supported
from backend.app.manufacturing.generation_selection import GroundedDraft
from backend.app.manufacturing.answer_context import attach_business_context, requested_execution_fields

OLD = ROOT/'tmp/manufacturing-gold-100-v4-grounded-live-20261113-r5'
NEW = ROOT/'tmp/manufacturing-gold-100-v4-answer-focus-live-20261113-r8'
PUBLIC_OLD = ROOT/'evals/manufacturing_gold_100_grounded_live_v1/runs/20261113-r5'
PUBLIC_NEW = ROOT/'evals/manufacturing_gold_100_answer_focus_live_v1/runs/20261113-r8'
NUMBERS = (2, 7, 10, 19, 22, 26, 27, 44, 48, 81, 83)


def main_post(record):
    return next(e for e in record['events'] if e['method']=='POST'
                and e['path'] in ('/api/qa', '/api/manufacturing/qa'))


def signature(rows):
    return [(r.get('chunk_id'), r.get('document_name'),
             hashlib.sha256(r.get('content', '').encode()).hexdigest()) for r in rows]


def describe(record):
    event = main_post(record); stages = event['trace']['stages']
    generated = stages.get('answer_generation', [{}])[0]
    return dict(route=event['path'], status=event['status_code'],
        final_answer=record['final']['response'].get('answer'),
        final_refusal=record['final']['response'].get('refusal_reason'),
        harness_error=record['harness_error'], selection=stages.get('manufacturing_selection'),
        context_count=len(stages.get('context', [])), context_status=stages.get('context_status'),
        first_claims=generated.get('claims'), coverage=generated.get('coverage'),
        first_validation_errors=generated.get('validation_errors'),
        completion_changed=generated.get('completion_changed'),
        decisions=stages.get('answer_decisions'), completion=stages.get('answer_completion'),
        routing=stages.get('rag_routing'),
        model_usage=[dict(stage=x['stage'], status=x['status'], total_tokens=x.get('total_tokens'),
                          error_type=x.get('details', {}).get('error_type'),
                          duration_ms=x.get('details', {}).get('duration_ms'))
                     for x in stages.get('model_usage', [])])


def frozen_context(record, question):
    context, active = reconstruct(record, question)
    if active and active.context.record.schema_version=='openmes-work-order-v1':
        app = next(e['response']['snapshot']['manufacturing']['applicability']
                   for e in record['events'] if e['method']=='GET'
                   and isinstance(e.get('response'), dict)
                   and e['response'].get('snapshot', {}).get('manufacturing'))
        selected = app.get('step_guidance')
        active.decision.scope.manufacturing.step_mapping = SimpleNamespace(selected=selected) if selected else None
    return context, active


def draft_for(record, question):
    context, active = frozen_context(record, question)
    stages = main_post(record)['trace']['stages']; gen = stages['answer_generation'][0]
    answerable = next(x['answerable'] for x in stages['answer_decisions'] if x['stage']=='generation')
    cls = GroundedDraft if active else EvidenceFirstAnswerDraft
    draft = cls.model_validate(dict(answerable=answerable,
        response_type='answer' if answerable else 'refuse', claims=gen['claims'],
        coverage=gen['coverage']['coverage']))
    return context, active, draft, gen


def check(draft, context, question):
    result = validate_output(draft, validator=AnswerValidator(), context=context, question=question)
    return dict(valid=result.valid, errors=result.errors)


def probe(record, case):
    n = int(case['id'][-3:]); stages = main_post(record)['trace']['stages']
    if n==2:
        return dict(generation_trace_saved=bool(stages.get('answer_generation')),
                    answer_generation_errors=[x for x in describe(record)['model_usage']
                                              if x['stage']=='answer_generation'],
                    limitation='No generated draft or supplier request ID; cannot infer semantic quality or the underlying provider timeout cause.')
    question=case['question'];context, active, draft, gen = draft_for(record, question)
    with manufacturing_answer_context(active):
        result=dict(original=check(draft, context, question))
        assert result['original']['errors']==gen['validation_errors'], case['id']
        result['business_fields']=[dict(citation_id=x.citation_id, field=x.field_name,
            value=x.value, source_entity=x.source_entity, content=x.content) for x in context.business_items
            if x.field_name in ('batch_id','batch_lot_number','job_card_id','job_card_actual_started_at',
                                'job_card_status','actual_started_at')]
        result['requested_execution_fields']=list(requested_execution_fields(question))
        if n==10:
            trimmed=draft.model_copy(deep=True);trimmed.claims.pop(1)
            result['drop_only_uncited_in_coverage_source_introduction_claim']=check(trimmed, context, question)
            result['numeric_rebinding_eligible']=numeric_binding_supported(
                draft, gen['validation_errors'], AnswerValidator(), context, question)
            assert result['drop_only_uncited_in_coverage_source_introduction_claim']['valid']
        if n==19:
            corrected=draft.model_copy(deep=True);corrected.coverage[0].missing_information=None
            result['only_missing_information_empty_string_to_null']=check(corrected, context, question)
            assert result['only_missing_information_empty_string_to_null']['valid']
            result['limitation']='Local validity still accepts the unchanged assertion 未开工; null normalization alone does not establish semantic correctness.'
        if n==7:
            claim=draft.claims[1]; cited=[x for x in context.items if x.citation_id in claim.citations]
            cited_text='\n'.join(x.content for x in cited)
            result['extra_comparison_support']=dict(claim=claim.text,
                claim_piece_quantities=sorted(AnswerValidator._manufacturing_quantities(claim.text)),
                cited_piece_quantities=sorted(AnswerValidator._manufacturing_quantities(cited_text)),
                cited_numeric_facts=sorted(AnswerValidator._extract_numeric_facts(
                    without_identifiers(cited_text, identifiers(cited_text)))),
                old_two_piece_source_hits=hits(stages['context'], '每次开工连续检查2件，全部项目合格后由质量人员签署。'),
                change_notice_comparison_hits=hits(stages['context'], '首件数量由连续2件改为连续3件'))
        if n==26:
            rendered=attach_business_context(context, question=question)
            result['mapping_guidance_visible_in_offline_render']=('依据为步骤映射审核，不是工单开工窗口' in rendered.text)
            result['backend_applicability_text']=active.applicability_text
        if n==83:
            result['answer_located_in_gap_instead_of_claims']=dict(claims=len(draft.claims),
                status=draft.coverage[0].status, missing_information=draft.coverage[0].missing_information,
                evidence_gap_recorded_in_trace=False)
            result['limitation']='Raw evidence_gap is not saved; feedback ineligible is observed, its exact parsed reason is not independently recoverable.'
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    out=parser.parse_args().out.resolve();out.relative_to(ROOT/'tmp');out.mkdir(exist_ok=False)
    gold=ROOT/'evals/manufacturing_gold_100_v4'
    cases={c['id']:c for c in map(json.loads,(gold/'cases.jsonl').read_text(encoding='utf-8').splitlines())}
    old_rows={r['case_id']:r for r in map(json.loads,(PUBLIC_OLD/'results.jsonl').read_text(encoding='utf-8').splitlines())}
    new_rows={r['case_id']:r for r in map(json.loads,(PUBLIC_NEW/'results.jsonl').read_text(encoding='utf-8').splitlines())}
    regressions={key for key in cases if old_rows[key]['review']['passed'] and not new_rows[key]['review']['passed']}
    assert regressions=={f'MFG-GOLD-{n:03}' for n in NUMBERS}
    protected=[gold/n for n in ('cases.jsonl','sources.json','review_policy.json','manifest.json')]
    for pub in (PUBLIC_OLD,PUBLIC_NEW):protected+=list(pub.glob('*'))
    for run in (OLD,NEW):
        for n in (*NUMBERS,86):
            protected += [run/(f'MFG-GOLD-{n:03}'+s) for s in ('-result.json','-stdout.log','-stderr.log')]
    code=['knowledge/answer_models.py','knowledge/answer_coverage.py','knowledge/answer_validation.py',
          'knowledge/answer_service.py','knowledge/answer_repair.py','knowledge/output_contract.py',
          'knowledge/answer_contract.py','knowledge/retrieval_service.py','knowledge/context_builder.py',
          'manufacturing/answer_context.py','manufacturing/generation_models.py',
          'manufacturing/generation_selection.py','manufacturing/source_statements.py']
    for name in code:
        current=ROOT/'backend/app'/name;frozen=NEW/'source/backend/app'/name
        assert current.read_bytes().replace(b'\r\n',b'\n')==frozen.read_bytes().replace(b'\r\n',b'\n'),name
        protected += [current,frozen]
    protected += [Path(__file__),ROOT/'scripts/diagnose_manufacturing_selection_failures.py']
    before={str(p.relative_to(ROOT)).replace('\\','/'):digest(p) for p in protected}
    rows=[]
    for n in NUMBERS:
        key=f'MFG-GOLD-{n:03}';a=read(OLD/(key+'-result.json'));b=read(NEW/(key+'-result.json'))
        sa=main_post(a)['trace']['stages'];sb=main_post(b)['trace']['stages']
        rows.append(dict(case_id=key, question=cases[key]['question'],old=describe(a),new=describe(b),
            ordered_inputs_equal={k:signature(sa.get(k,[]))==signature(sb.get(k,[]))
                                  for k in ('fusion','policy_candidates','rerank_model')
                                  if sa.get(k) or sb.get(k)},
            context_overlap=dict(old=len(sa.get('context',[])),new=len(sb.get('context',[])),
                same_document_content=len({(x[1],x[2]) for x in signature(sa.get('context',[]))}
                                          & {(x[1],x[2]) for x in signature(sb.get('context',[]))})),
            source_evidence=[dict(evidence_id=e['id'],quote=e['quote'],
                old_context_hits=hits(sa.get('context',[]),e['quote']),
                new_context_hits=hits(sb.get('context',[]),e['quote']))
                for e in cases[key]['evidence'] if e['kind']=='text'],
            old_review=old_rows[key]['review'],new_review=new_rows[key]['review'],
            local_probe=probe(b,cases[key])))
        if n in (19,22,26,27):
            ca,aa=frozen_context(a,cases[key]['question']);cb,ab=frozen_context(b,cases[key]['question'])
            def fields(context):
                return [(x.citation_id,x.field_name,x.value,x.content,x.source_entity)
                        for x in context.business_items if x.field_name!='fetched_at']
            rows[-1]['business_fields_except_fetch_timestamp_equal']=fields(ca)==fields(cb)
        if n==44:
            rows[-1]['review_consistency_probe']=dict(
                first_reading_in_old_delivered_answer='10.02' in (rows[-1]['old']['final_answer'] or ''),
                first_reading_in_new_delivered_answer='10.02' in (rows[-1]['new']['final_answer'] or ''),
                old_F02_passed=old_rows[key]['review']['required_facts'][1]['passed'],
                new_F02_passed=new_rows[key]['review']['required_facts'][1]['passed'],
                original_scores_modified=False,
                conclusion='Both delivered answers omit the first reading; different existing F02 judgments cannot establish a new generation omission.')
    supplemental={}
    for n in (86,):
        key=f'MFG-GOLD-{n:03}';a=read(OLD/(key+'-result.json'));b=read(NEW/(key+'-result.json'))
        supplemental[key]=dict(previously_failed=True,old=describe(a),new=describe(b),
                              source_evidence=cases[key]['evidence'])
    assert all(digest(ROOT/name)==value for name,value in before.items())
    result=dict(schema_version='manufacturing-answer-focus-regression-diagnosis-v1',
        old_source='4c9663b',new_source='ea00ed2',new_business='e755bcb',
        cases=rows,supplemental=supplemental,protected_inputs=len(before),input_sha256=before,
        inputs_unchanged=True,frozen_code_matches=code,model_calls=0,api_calls=0,database_calls=0,
        container_operations=0,network_connections_allowed=False,old_scores_recomputed=False,
        limits='Processed saved drafts/contexts only. Missing top-level parsed fields are inferred only where trace decisions establish them; no exact full-provider-request reconstruction or stochastic causality claim. Counterfactuals demonstrate local mechanisms, not new acceptance scores.')
    (out/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(regressions=len(rows),supplemental=len(supplemental),protected_inputs=len(before),
                          inputs_unchanged=True,model_calls=0,network=False)))


if __name__=='__main__':main()
