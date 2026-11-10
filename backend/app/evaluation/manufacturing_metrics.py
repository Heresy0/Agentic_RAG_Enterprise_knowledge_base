"""Additional manufacturing gates; frozen generic scoring stays unchanged."""
from .metrics import score_answer
from datetime import datetime
from ..manufacturing.answer_context import FIELD_LABELS

SCORER_VERSION = "manufacturing-workorder-v1"


def same_value(field, left, right):
    if field in ("source_updated_at", "fetched_at") and isinstance(left, str) and isinstance(right, str):
        try: return datetime.fromisoformat(left) == datetime.fromisoformat(right)
        except ValueError: return False
    return left == right


def score_manufacturing(case, response, context=None, *, execution_scorer='manufacturing-execution-v1'):
    if case['business_context_binding']['context']['record']['schema_version']=='openmes-work-order-v1':
        from .openmes_metrics import score_openmes
        return score_openmes(case,response,context)
    if case['business_context_binding']['context']['record']['schema_version'] == 'work-order-execution-v1':
        from .manufacturing_execution_metrics import score_execution
        # Frozen runners retain their original v1 unless a new job opts in.
        return score_execution(case, response, context, scorer_version=execution_scorer)
    result = score_answer(case, response, context)
    expected = case["business_context_binding"]["context"]
    record = expected["record"]
    actual = response.get("work_order_context") or {}
    failures = []
    fields = ("source_system_id", "source_record_id", "work_order_id", "plant_code", "source_revision")
    if actual.get("payload_sha256") != expected["payload_sha256"] or any(actual.get(key) != record[key] for key in fields):
        failures.append("business_identity")
    for citation in response.get("business_citations", []):
        field = citation.get("field_name")
        value = expected["fetched_at"] if field == "fetched_at" else record.get(field)
        if (any(citation.get(key) != record[key] for key in fields)
                or citation.get("payload_sha256") != expected["payload_sha256"]
                or field not in FIELD_LABELS or not same_value(field, citation.get("value"), value)):
            failures.append("business_citation")
    for evidence in case.get("expected_business_evidence", []):
        field = evidence["field_name"]
        if not same_value(field, actual.get(field), evidence["value"]):
            failures.append("business_field:" + field)
    if response.get("applicability") != case["applicability_binding"]["applicability"]:
        failures.append("applicability")
    result.update(manufacturing_scorer=SCORER_VERSION, manufacturing_failures=sorted(set(failures)))
    result["automatic_proxy_pass"] = bool(result["automatic_proxy_pass"] and not failures)
    return result


def score_frozen_workorder_case(case, response, context, source_record):
    """M0 benchmark shape; only score after the HTTP request has completed."""
    result = score_answer(case, response, context)
    actual = response.get("work_order_context") or {}
    expected = source_record.model_dump(mode="json")
    failures = []
    if actual.get("payload_sha256") != source_record.payload_sha256:
        failures.append("business_payload")
    for key,value in expected.items():
        if key not in ("schema_version", "tenant_id") and not same_value(key,actual.get(key),value):
            failures.append("business_record:" + key)
    for item in case.get("expected_business_evidence", []):
        if not same_value(item["field"],actual.get(item["field"]),item["value"]):
            failures.append("business_field:" + item["field"])
    for citation in response.get("business_citations", []):
        field = citation.get("field_name")
        value = actual.get("fetched_at") if field == "fetched_at" else expected.get(field)
        if (citation.get("payload_sha256") != source_record.payload_sha256 or
                any(citation.get(key) != expected[key] for key in ("source_system_id","source_record_id","work_order_id","plant_code","source_revision")) or
                field not in FIELD_LABELS or not same_value(field,citation.get("value"),value)):
            failures.append("business_citation")
    from .metrics import normalize
    forbidden = [value for value in case.get("forbidden_claims",[]) if normalize(value) in normalize(response["answer"])]
    if forbidden: failures.append("forbidden_claim")
    result.update(manufacturing_scorer=SCORER_VERSION,manufacturing_failures=sorted(set(failures)),forbidden_matches=forbidden)
    result["automatic_proxy_pass"] = bool(result["automatic_proxy_pass"] and not failures)
    return result
