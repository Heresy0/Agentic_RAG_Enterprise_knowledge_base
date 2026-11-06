"""Manufacturing-only generation schema; the ordinary schema stays unchanged."""
from typing import Literal
from pydantic import Field, model_validator
from ..knowledge.answer_models import AnswerClaim, ClaimKind, EvidenceFirstAnswerDraft
import json
import re


BusinessField = Literal[
    "work_order_id", "product_model", "operation_code", "actual_started_at", "actual_start_precision",
    "reported_at", "route_revision", "batch_no", "status", "plant_code", "change_notice_refs",
    "source_revision", "source_system_id", "source_record_id", "source_updated_at", "fetched_at",
]


class ManufacturingClaim(AnswerClaim):
    claim_kind: ClaimKind = Field(..., description="区分工单字段、适用判断、一般要求、版本对比及工单原文记录。")
    business_field: BusinessField | None = Field(default=None, exclude_if=lambda value: value is None,
        description="business_fact只选择一个字段名，text留空、citations为空数组；文字、值及业务引用由后端生成。其他类型为null。")
    text: str = Field(default="", description="工单字段选择及applicability留空，由后端生成；其他类型填写有来源的结论。")

    @model_validator(mode="after")
    def field_selection(self):
        if self.business_field is not None:
            if (self.claim_kind != "business_fact" or self.text.strip() or self.citations
                    or self.calculation is not None):
                raise ValueError("业务字段选择只接受字段名，不接受模型提供的值、文字、引用或计算。")
        elif not self.text.strip() and (self.claim_kind != "applicability" or self.calculation is not None):
            raise ValueError("非字段结论必须有文字，只有后端适用性结论可留空。")
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

    Field selectors carry no model-provided value or assertion. All other model
    conclusions still pass the original per-claim source and numeric validation.
    """
    from .answer_context import current_manufacturing_context
    from ..evaluation.trace import record_rag_event
    active = current_manufacturing_context()
    if active is None or not draft.answerable or not draft.claims:
        return draft
    fields = {item.field_name: item for item in context.business_items}
    field_bindings, rendered = [], []
    for claim in draft.claims:
        field = getattr(claim, "business_field", None)
        if field is not None:
            # The selected field must exist in this exact generation context.
            item = fields[field]
            claim = claim.model_copy(update=dict(text=item.content, citations=[item.citation_id], business_field=None))
            field_bindings.append(dict(field=field, compiled_citations=[item.citation_id], origin="server_business_field"))
        rendered.append(claim)
    if field_bindings:
        record_rag_event(dict(kind="manufacturing_binding_compilation", bindings=field_bindings, model_calls=0))
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
        if claim.claim_kind == "applicability" and not claim.text.strip() and claim.calculation is None:
            claim = claim.model_copy(update=dict(text=active.applicability_text))
            changed.append(dict(origin="server_applicability_selection", compiled_citations=citations))
        # A current-order subject is presentation, not a new applicability
        # decision. Normalize only an explicit requirement, never an execution
        # record or a statement about a different order.
        if claim.claim_kind == "work_requirement" and claim.calculation is None:
            subject = re.match(r"^(?:该工单|本工单|此工单|这张工单|工单" + re.escape(active.context.record.work_order_id)
                + r"(?![A-Za-z0-9_.:-]))(?:的)?[：:，,]?\s*(.+)$", claim.text.strip())
            if (subject and re.search(r"须|必须|应当|不得|不能|需要|要求", subject[1])
                    and not re.search(r"已经|已完成|已检|已放行|已恢复|实际完成|实际检验", subject[1])):
                claim = claim.model_copy(update=dict(text=subject[1]))
                changed.append(dict(origin="current_work_order_subject", compiled_citations=claim.citations))
        if (claim.claim_kind in ("applicability", "business_fact") and claim.text.rstrip("。") == active.applicability_text
                and claim.calculation is None and (claim.citations != citations or claim.claim_kind != "applicability")):
            changed.append(dict(original_citations=claim.citations,compiled_citations=citations))
            claim=claim.model_copy(update=dict(citations=list(citations),claim_kind="applicability"))
        parts = []
        if claim.claim_kind == "work_order_evidence" and claim.calculation is None and work_order:
            # Only compile complete, consecutive original sentences. A fragment,
            # paraphrase or other business field remains invalid for the validator.
            business_refs = set(claim.citations) & {item.citation_id for item in active.items}
            requested = [part.strip().strip("。！？；; \n") for part in re.split(r"[。！？；;\n]+", claim.text) if part.strip()]
            if business_refs <= {work_order} and requested:
                for item in context.items:
                    if item.citation_id not in claim.citations or item.document_role not in ("event_record", "inspection_record"):
                        continue
                    original = [part.strip().strip("。！？；; \n") for part in re.split(r"[。！？；;\n]+", item.content) if part.strip()]
                    if any(original[offset:offset+len(requested)] == requested for offset in range(len(original))):
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
