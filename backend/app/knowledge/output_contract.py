"""Shared output preparation and checks for generation and bounded repair."""
from typing import Literal

from pydantic import BaseModel, Field, create_model

from .answer_contract import direct_answer_errors
from .answer_models import AnswerClaim, EvidenceFirstAnswerDraft
from .answer_validation import interaction_errors


def generation_schema(context=None, *, claim=False):
    from ..manufacturing.answer_context import current_manufacturing_context
    manufacturing = current_manufacturing_context() is not None or context is not None and bool(context.business_items)
    if manufacturing:
        from ..manufacturing.generation_models import (ManufacturingClaim, ManufacturingGenerationDraft,
            ExecutionManufacturingClaim, ExecutionManufacturingGenerationDraft)
        active = current_manufacturing_context()
        if (active is not None and getattr(active.context.record,'schema_version',None)=='openmes-work-order-v1' or
                context is not None and any(row.field_name=='work_order_number' for row in context.business_items)):
            from ..manufacturing.generation_models import BatchManufacturingClaim, BatchManufacturingGenerationDraft
            return BatchManufacturingClaim if claim else BatchManufacturingGenerationDraft
        if (active is not None and getattr(active.context.record, 'execution', None) is not None or
                context is not None and any(row.field_name == 'operation_id' for row in context.business_items)):
            return ExecutionManufacturingClaim if claim else ExecutionManufacturingGenerationDraft
        return ManufacturingClaim if claim else ManufacturingGenerationDraft
    return AnswerClaim if claim else EvidenceFirstAnswerDraft


def reference_schema(context, *, claim_type=AnswerClaim, draft_type=EvidenceFirstAnswerDraft, claim=False):
    """Use the same current-reference contract for generation and bounded repair."""
    refs = tuple(dict.fromkeys(item.citation_id for item in [*context.items, *context.business_items]))
    model = create_model('Bound' + claim_type.__name__, __base__=claim_type,
        citations=(list[Literal[refs]], Field(default_factory=list,
            description='支持本条结论的当前授权来源编号；直接选择，不附加前后缀。')))
    return model if claim else create_model('Bound' + draft_type.__name__, __base__=draft_type,
        claims=(list[model], Field(default_factory=list)))


def live_generation_schema(context):
    if context is not None and context.business_items:
        from ..manufacturing.generation_selection import answer_schema
        return answer_schema(context)
    if context is not None and context.items:
        return reference_schema(context)
    return generation_schema(context)


def with_manufacturing_rules(system, context, *, draft=None):
    if context.business_items:
        from ..manufacturing.generation_selection import GroundedDraft
        if isinstance(draft, GroundedDraft):
            return system + '\n业务编号和资料编号均为当前授权来源。保持后端已编译事实、角色和文字；本任务只修复覆盖及引用，不切换为source选择。'
        from ..manufacturing.answer_context import MANUFACTURING_PROMPT
        return system + MANUFACTURING_PROMPT
    return system


def prepare_draft(result, context=None, *, schema=None):
    schema = schema or generation_schema(context)
    from ..manufacturing.generation_selection import (GroundedDraft, SelectionDraft, lower_selection, selection_schema,
                                                     LiveAnswerPlan, answer_schema, lower_answer_plan)
    if (context is not None and context.business_items and isinstance(result, dict)
            and any('source' in row for row in result.get('claims', []) if isinstance(row, dict))
            and any('text' in row or str(row.get('source','')).startswith('后端') for row in result['claims'])):
        result = answer_schema(context).model_validate(result)
    if isinstance(result, LiveAnswerPlan):
        result = lower_answer_plan(result, context)
    if (context is not None and context.business_items and isinstance(result, dict)
            and any('source' in row for row in result.get('claims', []) if isinstance(row, dict))):
        result = selection_schema(context).model_validate(result)
    if isinstance(result, SelectionDraft):
        result = lower_selection(result, context, schema)
    elif (context is not None and context.business_items and isinstance(result, dict)
          and not any('claim_kind' in row for row in result.get('claims', []))):
        result = GroundedDraft.model_validate(result)
    if isinstance(result, GroundedDraft):
        schema = GroundedDraft
    # Wire enums/subclasses do not carry authority into the internal draft.
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
    result = validator.validate(draft=draft, context=context, question=question, coverage=coverage)
    result.errors = list(dict.fromkeys([
        *result.errors, *result.coverage_errors,
        *direct_answer_errors(question, draft), *interaction_errors(draft),
    ]))
    result.valid = result.valid and not result.errors
    return result
