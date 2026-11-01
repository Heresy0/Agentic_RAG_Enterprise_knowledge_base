"""Read-only M4 evidence index and frozen acceptance report. Never calls models."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

# These are evidence pointers, not additional questions or accuracy samples.
BACKEND = {
    'A01':['all_manufacturing_and_snapshot_entries_require_login_with_positive_control'],
    'A02':['cross_tenant_http_cannot_use_another_tenants_connector'],
    'A03':['business_list_pagination_and_context_have_uniform_visibility'],
    'A04':['client_cannot_override_identity_or_source_facts'],
    'A05':['http_requires_business_and_document_grants_with_positive_control[knowledge_only]'],
    'A06':['http_requires_business_and_document_grants_with_positive_control[business_only]'],
    'A07':['restricted_quality_library_cannot_enter_http_model_or_snapshot_until_granted'],
    'A08':['restricted_quality_library_cannot_enter_http_model_or_snapshot_until_granted','vector_and_keyword_filters_are_applied_before_limit'],
    'A09':['current_parent_neighbor_companion_and_agent_merge_exclude_wrong_version_and_work_order','every_path_invalidates_selection_when_deciding_catalog_or_source_changes'],
    'A10':['every_path_invalidates_selection_when_deciding_catalog_or_source_changes'],
    'A11':['changes_during_retrieval_are_checked_before_main_generation','denied_business_guard_prevents_every_structured_model_attempt'],
    'A12':['generation_time_revocation_or_rule_withdrawal_stops_delivery'],
    'A13':['revoked_business_grant_hides_title_history_and_snapshot_and_regrant_cannot_resume'],
    'A14':['private_business_feedback_cannot_be_reviewed_until_explicitly_shared'],
    'A15':['old_dataset_stops_on_active_fixture_source_rule_or_permission_change[review]'],
    'A16':['dataset_requires_explicit_export_consent_before_creation','old_dataset_stops_on_active_fixture_source_rule_or_permission_change[consent]'],
    'A17':['queue_claim_rechecks_business_authorization_before_model','old_dataset_stops_on_active_fixture_source_rule_or_permission_change'],
    'A18':['job_result_and_human_review_recheck_current_business_scope','injected_fault_config_difference_human_review_verify_and_close'],
    'A19':['revoked_business_grant_hides_title_history_and_snapshot_and_regrant_cannot_resume'],
    'A20':['reflected_service_credential_is_never_exposed_as_a_business_field','source_loader_resolves_only_explicit_env_credentials_and_redacts_failures'],
    **{f'F{i:02}':['formal_http_faults_stop_delivery_preserve_usage_and_have_positive_control['+name] for i,name in enumerate(
        ['timeout-','unavailable-','credential-','missing-','conditions-','tenant-','oversize-','before_revision-','after_revision-','same_revision-','final_failure-'],1)},
    'U02':['work_order_and_other_owner_cannot_rebind_an_existing_conversation'],
    'U03':['bound_followups_persist_pair_and_restore_frozen_source_without_contacting_mes','quantity_followup_keeps_bound_order_and_old_edition'],
    'U04':['revoked_business_grant_hides_title_history_and_snapshot_and_regrant_cannot_resume'],
    'U08':['revoked_business_grant_hides_title_history_and_snapshot_and_regrant_cannot_resume'],
}
# All malformed variants and all asynchronous invalidation tests must pass.
BACKEND['F06']=['formal_http_faults_stop_delivery_preserve_usage_and_have_positive_control['+v for v in ['tenant-','plant-','type-','date-']]
BACKEND['F07']=['formal_http_faults_stop_delivery_preserve_usage_and_have_positive_control['+v for v in ['oversized-','truncated-']]
FRONTEND = {
    'F12':['M3 U06:'], 'U01':['M3 U01:'], 'U02':['M3 U02/U03:'],
    'U03':['M3 U02/U03:'], 'U04':['M3 U04:'], 'U05':['M3 U05:'],
    'U06':['M3 U06:','switching knowledge base clears memory','late document preview after scope switch'],
    'U07':['M3 U07:','citation preview uses authorized endpoint'], 'U08':['M3 U08:'],
}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values, p):
    return sorted(values)[math.ceil(p*len(values))-1] if values else None


def matrix(directories, frontend):
    observations=[]
    for directory in directories:
        for path in sorted(directory.glob('*.json')):
            row=read(path)
            observations.append(dict(node_id=row['node_id'],outcome=row['outcome'],path=str(path),sha256=sha(path),
                http_requests=len(row['http']),model_input_count=len(row['model_inputs'])))
    xml=ET.parse(frontend)
    ui=[dict(name=row.attrib['name'],passed=not any(row.find(tag) is not None for tag in ('failure','error','skipped')))
        for row in xml.iter('testcase')]
    rows=[]
    for identifier in [*[f'A{i:02}' for i in range(1,21)],*[f'F{i:02}' for i in range(1,13)],*[f'U{i:02}' for i in range(1,9)]]:
        pointers=[];missing=[]
        for key in BACKEND.get(identifier,[]):
            found=[row for row in observations if ('::test_'+key) in row['node_id']]
            if not found:missing.append('backend:'+key)
            pointers.extend(found)
        controls=[]
        for key in FRONTEND.get(identifier,[]):
            found=[row for row in ui if row['name'].startswith(key)]
            if not found:missing.append('frontend:'+key)
            controls.extend(found)
        rows.append(dict(id=identifier,passed=not missing and bool(pointers or controls) and all(row['outcome']=='passed' for row in pointers)
            and all(row['passed'] for row in controls),missing=missing,backend=pointers,frontend=controls))
    return dict(type='synthetic_handler_and_dom_evidence_not_model_accuracy',model_calls=0,checks=rows,
        passed=sum(row['passed'] for row in rows),total=40,frontend_junit_sha256=sha(frontend),
        limitation='HTTP handlers use isolated SQL/source/model substitutes; non-HTTP repository checks are identified by zero request counts. Browser observations and native PostgreSQL receipts are separate.')


def formal(directory, reviews_path):
    profile=read(ROOT/'evals/manufacturing_workorder_v1/profile.json')
    cases=[row for split in ('development','regression') for row in jsonl(ROOT/'evals/manufacturing_workorder_v1'/split/'single_turn.jsonl')]
    records=jsonl(directory/'records.jsonl');by_id={row['case_id']:row for row in records}
    if len(by_id)!=len(records) or set(by_id)!={row['id'] for row in cases}:raise ValueError('Formal denominator must contain all 40 unique frozen cases')
    reviews=jsonl(reviews_path);reviewed={row['case_id']:row for row in reviews}
    if len(reviewed)!=len(reviews):raise ValueError('Duplicate semantic reviews')
    for key,row in reviewed.items():
        if key not in by_id or row['response_binding']!=by_id[key].get('response_binding') or row['verdict'] not in ('passed','failed','uncertain') or not row['reason']:
            raise ValueError('Review is not bound to the actual response')
    proxy=lambda key:bool(by_id[key].get('metrics',{}).get('automatic_proxy_pass'))
    semantic=lambda key:reviewed.get(key,{}).get('verdict')=='passed'
    splits={name:[row['id'] for row in jsonl(ROOT/'evals/manufacturing_workorder_v1'/name/'single_turn.jsonl')] for name in ('development','regression')}
    counts={name:dict(total=len(ids),automatic_proxy_passed=sum(map(proxy,ids)),semantic_review_passed=sum(map(semantic,ids))) for name,ids in splits.items()}
    critical=profile['acceptance']['critical_cases']
    negatives=[row['id'] for row in cases if row['expected_behavior']!='answer']
    numeric=[row['id'] for row in cases if row['category']=='numeric_conditions']
    positive=[row['id'] for row in cases if row['answerable']]
    latencies=[by_id[key]['elapsed_ms'] for key in positive if semantic(key)]
    raw=read(directory/'report.json')
    gates=dict(complete=len(records)==40 and all(row.get('status')==200 for row in records),
        automatic_total=sum(map(proxy,by_id))>=36,semantic_total=sum(map(semantic,by_id))>=36,
        all_reviewed=set(reviewed)==set(by_id),critical_all=all(proxy(key) and semantic(key) for key in critical.values()),
        negative_all=all(proxy(key) and semantic(key) for key in negatives),numeric_all=all(proxy(key) and semantic(key) for key in numeric),
        source_p95=raw['source_p95_ms']<=500,answer_p95=bool(latencies) and percentile(latencies,.95)<=20000)
    for name,minimum in profile['acceptance']['minimum_split_passed'].items():
        gates[name+'_automatic']=counts[name]['automatic_proxy_passed']>=minimum
        gates[name+'_semantic']=counts[name]['semantic_review_passed']>=minimum
    return dict(type='frozen_40_single_batch',total=40,automatic_proxy_passed=sum(map(proxy,by_id)),semantic_review_passed=sum(map(semantic,by_id)),
        splits=counts,gates=gates,passed=all(gates.values()),
        failed_proxy_ids=[key for key in by_id if not proxy(key)],failed_review_ids=[key for key in by_id if not semantic(key)],
        critical={code:dict(case_id=key,automatic=proxy(key),semantic=semantic(key)) for code,key in critical.items()},
        negative_count=len(negatives),numeric_count=len(numeric),source_p95_ms=raw['source_p95_ms'],
        correct_answer_count=len(latencies),correct_answer_p50_ms=percentile(latencies,.5),correct_answer_p95_ms=percentile(latencies,.95),
        correct_answer_max_ms=max(latencies) if latencies else None,usage=raw['usage'],budget=raw['budget'],
        reviews_sha256=sha(reviews_path),records_sha256=sha(directory/'records.jsonl'),reviewers=sorted({row['reviewer'] for row in reviews}),
        limitation='Assistant semantic review is non-independent and does not replace frozen automatic gates. No retries, score replacement or new model calls.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--logic-evidence',type=Path,nargs='+',required=True)
    p.add_argument('--frontend-junit',type=Path,required=True)
    p.add_argument('--formal-dir',type=Path,required=True)
    p.add_argument('--reviews',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();out=a.output_dir.resolve();out.relative_to(ROOT/'tmp');out.mkdir(parents=True,exist_ok=False)
    for name,data in [('matrix.json',matrix(a.logic_evidence,a.frontend_junit)),('formal.json',formal(a.formal_dir,a.reviews))]:
        with (out/name).open('x',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
        print(json.dumps(dict(artifact=name,total=data['total'],passed=data['passed']),ensure_ascii=False))


if __name__=='__main__':main()
