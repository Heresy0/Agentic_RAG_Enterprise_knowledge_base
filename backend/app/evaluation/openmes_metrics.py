"""OpenMES field/entity scoring, independent of the frozen ERP scorers."""
from .metrics import score_answer
from .manufacturing_metrics import same_value
from ..manufacturing.execution_contracts import context_from_payload
from ..manufacturing.answer_context import ManufacturingAnswerContext,business_citation

SCORER_VERSION='manufacturing-openmes-v1'


def score_openmes(case,response,context=None):
    result=score_answer(case,response,context)
    expected=context_from_payload(case['business_context_binding']['context'])
    record=expected.record;actual=response.get('work_order_context') or {};failures=[]
    values=record.model_dump(mode='json',exclude={'tenant_id','schema_version'})
    if actual.get('payload_sha256')!=expected.payload_sha256 or any(actual.get(k)!=v for k,v in values.items()):
        failures.append('business_identity')
    options={item.field_name:business_citation(item,expected) for item in ManufacturingAnswerContext(expected,None).items}
    def matches(citation,source):
        return source is not None and all(same_value(k,citation.get(k),v) for k,v in source.items() if k not in ('citation_id','last_verified_at'))
    citations=response.get('business_citations',[])
    for citation in citations:
        if not matches(citation,options.get(citation.get('field_name'))):failures.append('business_citation')
    for evidence in case.get('expected_business_evidence',[]):
        field=evidence['field_name'];source=options.get(field)
        if source is None or not same_value(field,evidence['value'],source['value']):failures.append('business_field:'+field)
        if field not in ('work_order_id','batch_id','step_id') and not any(c.get('field_name')==field and matches(c,source) for c in citations):
            failures.append('business_evidence:'+field)
    if response.get('applicability')!=case['applicability_binding']['applicability']:failures.append('applicability')
    result.update(manufacturing_scorer=SCORER_VERSION,manufacturing_failures=sorted(set(failures)))
    result['automatic_proxy_pass']=bool(result['automatic_proxy_pass'] and not failures)
    return result
