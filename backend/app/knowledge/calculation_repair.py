"""Conservative repairs backed by literal evidence and the existing calculator."""
import re
from decimal import Decimal

from .calculation_service import CalculationError, compute, normalized


def direct_quote(text, citations, context):
    # Whole sentences/paragraphs only: never accept a substring that drops negation.
    target = normalized(text).strip('。；;')
    return bool(target and any(target in {
        normalized(line).strip('。；;') for line in
        [item.content, *item.content.splitlines(), *re.split(r'[。；;]', item.content)]
    } for item in context.items if item.citation_id in citations))


def normalize_calculations(draft, context, question):
    candidate = draft.model_copy(deep=True)
    for claim in candidate.claims:
        if claim.calculation is None:
            continue
        # A verbatim factual sentence needs no calculation; it still passes normal validation.
        if direct_quote(claim.text, claim.citations, context):
            claim.calculation = None
            continue
        proposed = claim.model_copy(deep=True)
        for operand in proposed.calculation.inputs:
            if operand.source in ('用户问题', '用户输入'):
                operand.source = '问题'
            if operand.unit in ('秒', '分钟', '小时'):
                quantities = re.findall(r'(?<![\d.\-])(-?\d+(?:\.\d+)?)(秒|分钟|小时)', normalized(operand.quote))
                if len(quantities) == 1:
                    value, unit = quantities[0]
                    try:
                        factors = {'秒': 1, '分钟': 60, '小时': 3600}
                        if Decimal(value) * factors[unit] == Decimal(operand.value) * factors[operand.unit]:
                            operand.value, operand.unit = value, unit
                    except Exception:
                        pass
            if operand.source != '问题' and operand.source not in proposed.citations:
                proposed.citations.append(operand.source)
        try:
            computed = compute(proposed, context, question)
        except CalculationError:
            continue
        proposed.text = computed.rendered_text
        claim.text, claim.citations, claim.calculation = proposed.text, proposed.citations, proposed.calculation
    return candidate


def verified_calculation_repair(original, candidate, context, question):
    if (not candidate.answerable or candidate.refusal_reason != original.refusal_reason
            or candidate.evidence_gap != original.evidence_gap or not candidate.claims):
        return False
    operations = {claim.calculation.operation for claim in original.claims if claim.calculation}
    needed_operations = {claim.calculation.operation for claim in original.claims
                         if claim.calculation and not direct_quote(claim.text, claim.citations, context)}
    if not needed_operations.issubset({claim.calculation.operation for claim in candidate.claims if claim.calculation}):
        return False
    try:
        for claim in candidate.claims:
            if claim.calculation is None:
                if not direct_quote(claim.text, claim.citations, context):
                    return False
            elif claim.calculation.operation not in operations:
                return False
            else:
                claim.text = compute(claim, context, question).rendered_text
    except CalculationError:
        return False
    return True
