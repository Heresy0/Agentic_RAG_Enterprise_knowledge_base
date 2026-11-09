"""Manufacturing-only generation schema; the ordinary schema stays unchanged."""
from typing import Literal
from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from ..knowledge.answer_models import AnswerClaim, ClaimKind, EvidenceFirstAnswerDraft
import json


BusinessField = Literal[
    "work_order_id", "product_model", "operation_code", "actual_started_at", "actual_start_precision",
    "reported_at", "route_revision", "batch_no", "status", "plant_code", "change_notice_refs",
    "source_revision", "source_system_id", "source_record_id", "source_updated_at", "fetched_at",
]


class ManufacturingClaim(AnswerClaim):
    claim_kind: ClaimKind = Field(..., description="区分字段、适用性、一般要求、版本对比及记录。选择source_unit时除明确版本对比外，实际角色由后端按资料确定。")
    business_field: BusinessField | None = Field(default=None, exclude_if=lambda value: value is None,
        description="只选择一个工单字段名，文字、值、角色及业务引用完全由后端生成；附带的text/citations不作为事实输入。不能与source_unit或calculation混用。")
    text: str = Field(default="", description="business_field、source_unit选择及applicability留空，由后端生成；仅旧格式填写完整原文结论。")
    citations: list[str] = Field(default_factory=list,
        description="business_field、source_unit选择及applicability均填写[]，引用由后端生成；仅旧格式填写支持原文的资料编号。")
    source_unit: str | None = Field(default=None, max_length=80, exclude_if=lambda value: value is None,
        description="优先选择上下文标记的完整原文条目，如资料1#2。仅选择标识，text留空、citations=[]、calculation=null；未使用时填null或空字符串。后端生成原文、引用及来源角色。")

    @field_validator("source_unit", mode="before")
    @classmethod
    def unused_source_unit(cls, value):
        # A blank optional selector makes no assertion and grants no source.
        # Required prose/field selection is still checked below.
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def field_selection(self):
        selected = self.source_unit is not None or self.business_field is not None
        if self.source_unit is not None and self.business_field is not None:
            raise PydanticCustomError("manufacturing_selector_conflict", "业务字段和文档原文条目不能同时选择。")
        if selected and self.calculation is not None:
            raise PydanticCustomError("manufacturing_selector_calculation", "来源选择不能与计算混用。")
        if not selected and not self.text.strip() and (self.claim_kind != "applicability" or self.calculation is not None):
            raise PydanticCustomError("manufacturing_empty_claim", "非字段结论必须有文字，只有后端适用性结论可留空。")
        return self


class ManufacturingGenerationDraft(EvidenceFirstAnswerDraft):
    claims: list[ManufacturingClaim] = Field(default_factory=list)


def precision_projection(question, context):
    """Known date + known precision answers a clock question without inventing a time."""
    from .answer_context import current_manufacturing_context, precision_fact_question
    from ..knowledge.answer_coverage import requirement_prompt
    active = current_manufacturing_context()
    if (active is None or not active.facts_only or active.context.record.actual_start_precision != "date"
            or not precision_fact_question(question)):
        return None
    items = [item for item in active.items if item.field_name in ("actual_started_at","actual_start_precision")]
    requirements = json.loads(requirement_prompt(question,context))
    return ManufacturingGenerationDraft.model_validate(dict(answerable=True,response_type="answer",
        claims=[dict(business_field=item.field_name,claim_kind="business_fact") for item in items],
        coverage=[dict(requirement_id=row["id"],aspect=row["question"],status="answered",claim_indices=[1,2],missing_information=None) for row in requirements]))


def compile_applicability_bindings(draft, context):
    """Compile the backend's exact selection as scoped answer context.

    Explicit selectors never use model-provided display text or references as
    facts. Legacy assertions still pass the per-claim source/numeric validator.
    """
    from .answer_context import current_manufacturing_context, complete_source_statement
    from .source_statements import source_units
    from ..evaluation.trace import record_rag_event
    active = current_manufacturing_context()
    if active is None or not draft.answerable or not draft.claims:
        return draft
    fields = {item.field_name: item for item in context.business_items}
    units = source_units(context)
    field_bindings, rendered = [], []
    for claim in draft.claims:
        selected_unit = getattr(claim, "source_unit", None)
        ignored_display = [name for name in ("text", "citations") if getattr(claim, name)]
        if selected_unit in units:
            item, original = units[selected_unit]
            is_record = item.document_role in ("event_record", "inspection_record")
            kind = ("version_comparison" if claim.claim_kind == "version_comparison" else
                    "work_order_evidence" if is_record else "work_requirement")
            claim = claim.model_copy(update=dict(text=original, citations=[item.citation_id],
                claim_kind=kind, source_unit=None))
            field_bindings.append(dict(source_unit=selected_unit, compiled_citations=[item.citation_id],
                claim_kind=kind, ignored_display_fields=ignored_display, origin="server_source_unit"))
        elif selected_unit is not None:
            # An unresolved selector must never fall back to any accompanying
            # model assertion, including during final base-schema validation.
            claim = claim.model_copy(update=dict(text="", citations=[]))
        field = getattr(claim, "business_field", None)
        if field is not None:
            # The selected field must exist in this exact generation context.
            item = fields[field]
            claim = claim.model_copy(update=dict(text=item.content, citations=[item.citation_id],
                business_field=None, claim_kind="business_fact"))
            field_bindings.append(dict(field=field, compiled_citations=[item.citation_id],
                ignored_display_fields=ignored_display, origin="server_business_field"))
        rendered.append(claim)
    if field_bindings:
        record_rag_event(dict(kind="manufacturing_binding_compilation", bindings=field_bindings, model_calls=0))
    if rendered != draft.claims:
        draft = draft.model_copy(update=dict(claims=rendered))
    if active.facts_only or active.decision.state != "applicable":
        return draft
    required = {str(row.document_id) for row in active.decision.scope.manufacturing.documents
        if row.document_role == "change_notice" or row.document_role == "instruction" and row.business_revision == active.decision.selected_business_revision}
    available = {str(item.document_id) for item in context.items}
    if not required or not required <= available:return draft
    citations = [item.citation_id for item in context.items if str(item.document_id) in required]
    citations += [item.citation_id for item in active.items if item.field_name in active.required_fields]
    changed=[];claims=[];indices={}
    work_order = next((item.citation_id for item in active.items if item.field_name == "work_order_id"), None)
    for index, claim in enumerate(draft.claims, 1):
        start = len(claims) + 1
        if getattr(claim, "source_unit", None) is not None:
            claims.append(claim)  # Unresolved selection stays invalid for every role hint.
            indices[index] = [start]
            continue
        if claim.claim_kind == "applicability" and not claim.text.strip() and claim.calculation is None:
            claim = claim.model_copy(update=dict(text=active.applicability_text))
            changed.append(dict(origin="server_applicability_selection", compiled_citations=citations))
        if (claim.claim_kind in ("applicability", "business_fact") and claim.text.rstrip("。") == active.applicability_text
                and claim.calculation is None and (claim.citations != citations or claim.claim_kind != "applicability")):
            changed.append(dict(original_citations=claim.citations,compiled_citations=citations))
            claim=claim.model_copy(update=dict(citations=list(citations),claim_kind="applicability"))
        parts = []
        if claim.claim_kind == "work_order_evidence" and claim.calculation is None and work_order:
            # Only compile complete, consecutive original sentences. A fragment,
            # paraphrase or other business field remains invalid for the validator.
            business_refs = set(claim.citations) & {item.citation_id for item in active.items}
            requested = [part.strip() for part in claim.text.split("。") if part.strip()]
            if business_refs <= {work_order} and requested:
                for item in context.items:
                    if item.citation_id not in claim.citations or item.document_role not in ("event_record", "inspection_record"):
                        continue
                    if complete_source_statement(claim.text, [item]):
                        parts = requested
                        break
        if parts:
            refs = list(dict.fromkeys([*claim.citations, work_order]))
            claims.extend(claim.model_copy(update=dict(text=part, citations=list(refs))) for part in parts)
            if len(parts) > 1 or refs != claim.citations:
                changed.append(dict(original_citations=claim.citations,compiled_citations=refs,origin="exact_record_sentences",sentence_count=len(parts)))
        else:
            claims.append(claim)
        indices[index] = list(range(start, len(claims)+1))
    if not active.comparison and not any(claim.claim_kind == "applicability" for claim in claims):
        # Selection is a backend fact. Work guidance consistently includes its
        # scope and both source types even if the model only states a rule.
        claims.append(ManufacturingClaim(text=active.applicability_text,citations=list(citations),claim_kind="applicability"))
        changed.append(dict(original_citations=[],compiled_citations=citations,origin="server_applicability"))
    if changed:
        record_rag_event(dict(kind="manufacturing_binding_compilation",bindings=changed,model_calls=0))
        update = dict(claims=claims)
        if hasattr(draft, "coverage"):
            update["coverage"] = [row.model_copy(update=dict(claim_indices=[new for old in row.claim_indices for new in indices.get(old, [old])])) for row in draft.coverage]
        return draft.model_copy(update=update)
    return draft
