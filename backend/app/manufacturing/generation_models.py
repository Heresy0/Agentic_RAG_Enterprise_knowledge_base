"""Manufacturing-only generation schema; the ordinary schema stays unchanged."""
from pydantic import Field
from ..knowledge.answer_models import AnswerClaim, ClaimKind, EvidenceFirstAnswerDraft
import json
import re


class ManufacturingClaim(AnswerClaim):
    claim_kind: ClaimKind = Field(..., description="区分工单字段、适用判断、一般要求、版本对比及工单原文记录。")


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
        claims=[dict(text=item.content,citations=[item.citation_id],claim_kind="business_fact") for item in items],
        coverage=[dict(requirement_id=row["id"],aspect=row["question"],status="answered",claim_indices=[1,2],missing_information=None) for row in requirements]))


def compile_applicability_bindings(draft, context):
    """Compile the backend's exact selection as scoped answer context.

    This does not rewrite model conclusions, expand the selected document set or
    normalize false numeric claims. The usual validator still checks the result.
    """
    from .answer_context import current_manufacturing_context
    from ..evaluation.trace import record_rag_event
    active = current_manufacturing_context()
    if active is None or active.facts_only or active.decision.state != "applicable" or not draft.answerable or not draft.claims:return draft
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
