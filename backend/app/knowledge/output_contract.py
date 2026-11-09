"""Shared output preparation and checks for generation and bounded repair."""
from pydantic import BaseModel

from .answer_contract import direct_answer_errors
from .answer_models import AnswerClaim, AnswerDraft, EvidenceFirstAnswerDraft
from .answer_validation import interaction_errors


def generation_schema(context=None, *, claim=False):
    from ..manufacturing.answer_context import current_manufacturing_context
    manufacturing = current_manufacturing_context() is not None or context is not None and bool(context.business_items)
    if manufacturing:
        from ..manufacturing.generation_models import (ManufacturingClaim, ManufacturingGenerationDraft,
            ExecutionManufacturingClaim, ExecutionManufacturingGenerationDraft)
        active = current_manufacturing_context()
        if (active is not None and getattr(active.context.record, 'execution', None) is not None or
                context is not None and any(row.field_name == 'operation_id' for row in context.business_items)):
            return ExecutionManufacturingClaim if claim else ExecutionManufacturingGenerationDraft
        return ManufacturingClaim if claim else ManufacturingGenerationDraft
    return AnswerClaim if claim else EvidenceFirstAnswerDraft


def with_manufacturing_rules(system, context):
    if context.business_items:
        from ..manufacturing.answer_context import MANUFACTURING_PROMPT
        return system + MANUFACTURING_PROMPT
    return system


def prepare_draft(result, context=None, *, schema=None):
    schema = schema or generation_schema(context)
    if not isinstance(result, schema):
        result = schema.model_validate(result.model_dump() if isinstance(result, BaseModel) else result)
    # Unused diagnostic quotes and a redundant search request cannot turn a
    # legitimate clarification into a refusal. Assertions still undergo validation.
    if any(row.status != "answered" and row.source_quote is not None for row in result.coverage):
        result = result.model_copy(update={"coverage": [
            row.model_copy(update={"source_quote": None}) if row.status != "answered" else row
            for row in result.coverage]})
    if result.response_type == "clarify" and result.evidence_gap is not None:
        result = result.model_copy(update={"evidence_gap": None})
    if context is not None and context.business_items:
        from ..manufacturing.generation_models import compile_applicability_bindings
        result = compile_applicability_bindings(result, context)
    return result


def validate_output(draft, *, validator, context, question, coverage=None):
    # Final supplements must not replace the first-pass coverage verdict.
    checked = draft if coverage is None else AnswerDraft.model_validate(draft.model_dump(exclude={"coverage"}))
    result = validator.validate(draft=checked, context=context, question=question)
    if coverage is not None:
        result.coverage_errors = list(coverage)
    result.errors = list(dict.fromkeys([
        *result.errors, *result.coverage_errors,
        *direct_answer_errors(question, draft), *interaction_errors(draft),
    ]))
    result.valid = result.valid and not result.errors
    return result
