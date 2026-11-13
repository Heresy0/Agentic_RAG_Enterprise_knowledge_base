"""Explicit offline readers for historical generation drafts; never used by live QA."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, create_model
from backend.app.knowledge.answer_models import EvidenceFirstAnswerDraft
from backend.app.knowledge.output_contract import generation_schema
from backend.app.manufacturing.generation_selection import (
    SourceSelection, GroundedDraft, LiveAnswerPlan, answer_schema, lower_answer_plan,
)
from backend.app.manufacturing.source_statements import source_units


class SelectionDraft(EvidenceFirstAnswerDraft):
    model_config = ConfigDict(extra="forbid")
    claims: list[SourceSelection] = Field(default_factory=list)


def selection_schema(context):
    """Enumerate only sources already authorized for this exact request."""
    refs = tuple(dict.fromkeys([
        *(item.citation_id for item in context.business_items), *source_units(context),
    ]))
    if not refs:
        raise ValueError("制造来源选择需要已绑定的上下文")
    claim = create_model("ManufacturingSourceSelection", __base__=SourceSelection,
                         source=(Literal[refs], Field(description=SourceSelection.model_fields['source'].description)))
    return create_model("ManufacturingSelectionDraft", __base__=SelectionDraft,
                        claims=(list[claim], Field(default_factory=list,
                            description="仅选择回答问题所需来源；文字、角色、引用由后端生成。")))


def lower_selection(draft, context, legacy_schema):
    # Revalidate even a previously parsed model against the current request.
    # A stale or other-request selector must never reuse its old authorization.
    selected = selection_schema(context).model_validate(draft.model_dump())
    fields = {item.citation_id: item.field_name for item in context.business_items}
    claims = [dict(claim_kind="business_fact", business_field=fields[row.source])
              if row.source in fields else dict(claim_kind="work_requirement", source_unit=row.source)
              for row in selected.claims]
    return legacy_schema.model_validate(selected.model_dump(exclude={"claims"}) | {"claims": claims})


def prepare_legacy_draft(result, context=None, *, schema=None):
    schema = schema or generation_schema(context)
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
        from backend.app.manufacturing.generation_models import compile_applicability_bindings
        result = compile_applicability_bindings(result, context)
    return result

