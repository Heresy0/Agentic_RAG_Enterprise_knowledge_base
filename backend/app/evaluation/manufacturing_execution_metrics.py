"""Execution-aware scoring; legacy work-order scorer and frozen rules stay unchanged."""
from .metrics import score_answer
from .manufacturing_metrics import same_value
from ..manufacturing.execution_contracts import context_from_payload
from ..manufacturing.answer_context import ManufacturingAnswerContext, business_citation

LEGACY_SCORER_VERSION = 'manufacturing-execution-v1'
SCORER_VERSION = 'manufacturing-execution-v2'


def score_execution(case, response, context=None, *, scorer_version=SCORER_VERSION):
    if scorer_version not in (LEGACY_SCORER_VERSION, SCORER_VERSION):
        raise ValueError('未知的执行字段评分版本。')
    def matches_value(field, left, right):
        # HTTP JSON carries ordered arrays; typed business records use tuples.
        # Do not coerce nulls/scalars, reorder entries or change frozen v1.
        if scorer_version == SCORER_VERSION and isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
            return list(left) == list(right)
        return same_value(field, left, right)
    result = score_answer(case, response, context)
    expected = context_from_payload(case['business_context_binding']['context'])
    record = expected.record
    actual = response.get('work_order_context') or {}
    actual_execution = actual.get('execution') or {}
    failures = []
    if (actual.get('payload_sha256') != expected.payload_sha256 or any(actual.get(key) != getattr(record, key)
            for key in ('source_system_id', 'source_record_id', 'work_order_id', 'plant_code', 'source_revision'))
            or actual_execution != record.execution.model_dump(mode='json')):
        failures.append('business_identity')
    options = {row.field_name: business_citation(row, expected) for row in ManufacturingAnswerContext(expected, None).items}

    def matches(citation, source):
        return source is not None and all(matches_value(key, citation.get(key), value) for key, value in source.items()
            if key not in ('citation_id', 'last_verified_at'))

    citations = response.get('business_citations', [])
    for citation in citations:
        if not matches(citation, options.get(citation.get('field_name'))):
            failures.append('business_citation')
    for evidence in case.get('expected_business_evidence', []):
        field = evidence['field_name']
        value = actual_execution.get(field) if field in type(record.execution).model_fields else actual.get(field)
        if not matches_value(field, value, evidence['value']):
            failures.append('business_field:' + field)
        # Selection IDs are anchors verified in the full context and each field
        # citation. They need not be repeated as separate prose assertions.
        if field not in ('operation_id', 'job_card_id') and not any(
                matches(citation, options.get(field)) and citation.get('field_name') == field for citation in citations):
            failures.append('business_evidence:' + field)
    if response.get('applicability') != case['applicability_binding']['applicability']:
        failures.append('applicability')
    result.update(manufacturing_scorer=scorer_version, manufacturing_failures=sorted(set(failures)))
    result['automatic_proxy_pass'] = bool(result['automatic_proxy_pass'] and not failures)
    return result
