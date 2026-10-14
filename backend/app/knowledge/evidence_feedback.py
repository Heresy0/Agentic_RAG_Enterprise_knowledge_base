"""A single declared evidence-gap search followed by one budgeted regeneration."""
import logging
import re
import time
import unicodedata
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError

from ..evaluation.trace import (capture_trace, record_feedback_retrieval, record_documents,
                                record_rag_event, record_scope)
from ..security.authorization import AuthorizationDenied
from .answer_coverage import coverage_errors, question_requirements
from .answer_models import AnswerDraft
from .answer_validation import AnswerValidator
from .evidence_coordinator import EvidenceAccess
from .evidence_selection import select_evidence
from .rag_budget import current_rag_budget, query_key, RagBudgetExceeded
from .request_budget import evidence_deadline
from .temporal import future_certainty_gap

logger = logging.getLogger(__name__)


def context_signature(context):
    return [(item.chunk_id, item.document_id, item.knowledge_base_id, item.content, item.policy)
            for item in context.items]


def _content_key(content):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", content))


def eligible_gap(draft, question, context):
    gap = draft.evidence_gap
    if (gap is None or gap.reason != "missing_evidence" or draft.answerable or draft.claims
            or not (draft.refusal_reason or "").strip() or coverage_errors(draft, question, context)):
        return False
    insufficient = {row.requirement_id for row in draft.coverage if row.status == "insufficient"}
    ids = gap.requirement_ids
    return bool(ids and len(ids) == len(set(ids)) and set(ids) <= insufficient
                and gap.source_quote and gap.source_quote.strip() and gap.source_quote in question)


def normalize_decline_coverage(draft, question, context):
    """An overall refusal has no delivered answer: known parts are withheld.

    Only a missing-evidence refusal with a real insufficient row qualifies.
    Only verified partial claims can be cleared in the feedback-only copy.
    Never change insufficient rows, invent missing facts, or mutate the saved draft.
    """
    candidate = draft.model_copy(deep=True)
    if (draft.answerable or draft.evidence_gap is None
            or draft.evidence_gap.reason != "missing_evidence"
            or not any(row.status == "insufficient" for row in draft.coverage)):
        return candidate, 0
    if draft.claims:
        partial = AnswerDraft(answerable=True, claims=draft.model_copy(deep=True).claims)
        if not AnswerValidator().validate(draft=partial, context=context, question=question).valid:
            return candidate, 0
        # These facts are not passed to regeneration or served as an answer.
        candidate.claims = []
    changed = 0
    for row in candidate.coverage:
        if (row.status == "answered" and len(row.claim_indices) == len(set(row.claim_indices))
                and all(1 <= index <= len(draft.claims) for index in row.claim_indices)):
            row.status, row.claim_indices = "withheld", []
            row.missing_information = "因整体拒答未输出该要点"
            changed += 1
    return candidate, changed


@dataclass
class FeedbackResult:
    draft: object
    documents: list
    context: object
    regenerated: bool = False
    search_ms: float | None = None
    regeneration_ms: float | None = None


class EvidenceFeedback:
    def __init__(self, settings, retrieval, coordinator, context_builder, *, guard_factory=EvidenceAccess):
        self.settings, self.retrieval, self.coordinator = settings, retrieval, coordinator
        self.builder, self.guard_factory = context_builder, guard_factory

    def run(self, draft, *, question, documents, context, scope, session, preflight, regenerate):
        if not getattr(self.settings, "rag_feedback_enabled", False) or draft.evidence_gap is None:
            return None
        result = FeedbackResult(draft, documents, context)
        normalized, changed = normalize_decline_coverage(draft, question, context)
        if not eligible_gap(normalized, question, context):
            record_rag_event(dict(kind="feedback", outcome="ineligible"))
            return result
        if changed:
            record_rag_event(dict(kind="feedback_normalization", withheld_rows=changed,
                                 discarded_partial_claims=len(draft.claims)))
        guard, budget = self.guard_factory(session, scope), current_rag_budget()
        if not guard.available or budget is None:
            record_rag_event(dict(kind="feedback", outcome="identity_or_budget_missing"))
            return result
        try:
            guard.require()
            quote = draft.evidence_gap.source_quote
            if query_key(quote) == query_key(question):
                # Narrow only to one explicitly declared missing aspect that is
                # itself a literal original-question span. Never invent a query.
                aspects = {row.aspect.strip('？?。 ') for row in draft.coverage
                           if row.status == 'insufficient'
                           and row.requirement_id in draft.evidence_gap.requirement_ids}
                anchors = {aspect for aspect in aspects if 2 <= len(aspect) <= 200
                           and aspect in question and query_key(aspect) != query_key(question)}
                if len(anchors) == 1:
                    quote = next(iter(anchors))
                    record_rag_event(dict(kind='feedback_anchor', outcome='declared_aspect', source_quote=quote))
            query = f"重点查证：{quote}\n原始问题：{question}"
            if (query_key(quote) == query_key(question) or query_key(quote) in budget.seen_queries
                    or query_key(query) in budget.seen_queries):
                record_rag_event(dict(kind="feedback", outcome="repeated_query"))
                return result
            # Check a current-context regeneration reservation before spending on search.
            # The actual enlarged prompt is checked again after retrieval.
            preflight(context)
            current = self.retrieval.refresh_evidence(documents, scope=scope, session=session)
            if context_signature(self.builder.build(current, question=question)) != context_signature(context):
                record_rag_event(dict(kind="feedback", outcome="evidence_changed"))
                return result
            budget.begin_feedback()
            record_rag_event(dict(kind="strategy_transition", selected_strategy="agentic", reason="generation_evidence_gap"))
            record_rag_event(dict(kind="feedback_query", requirement_ids=draft.evidence_gap.requirement_ids,
                                 source_quote=quote, query=query))
            tick = time.perf_counter()
            tool_trace = None
            try:
                with budget.tool_attempt("search", query=query) as remaining, \
                        evidence_deadline(time.monotonic() + remaining), capture_trace() as tool_trace:
                    extra = self.retrieval.search(query, scope=scope, session=session)
            finally:
                result.search_ms = round((time.perf_counter() - tick) * 1000, 2)
                if tool_trace is not None:
                    record_feedback_retrieval(tool_trace.to_dict())
            guard.require()
            # A document retired during the search cannot silently disappear from
            # this request's existing evidence before regeneration.
            original_now = self.retrieval.refresh_evidence(current, scope=scope, session=session)
            if context_signature(self.builder.build(original_now, question=question)) != context_signature(context):
                record_rag_event(dict(kind="feedback", outcome="evidence_changed_after_search"))
                return result
            merged = self.coordinator._merge(current, extra, scope, interleave=True)
            merged = self.retrieval.refresh_evidence(merged, scope=scope, session=session)
            merged = select_evidence(question, merged)
            enlarged = self.builder.build(merged, question=question)
            if not all(item in context_signature(enlarged) for item in context_signature(context)):
                record_rag_event(dict(kind="feedback", outcome="required_evidence_lost"))
                return result
            if future_certainty_gap(question, enlarged.items):
                record_rag_event(dict(kind="feedback", outcome="future_policy_gap"))
                return result
            existing_content = {_content_key(item.content) for item in context.items}
            if not any(item.evidence_role != "front_matter" and _content_key(item.content) not in existing_content
                       for item in enlarged.items):
                record_rag_event(dict(kind="feedback", outcome="no_new_evidence"))
                return result
            # New context must not drop the original table's mandatory facets.
            old_facets = {row.text for row in question_requirements(question, context) if row.citation_id}
            new_facets = {row.text for row in question_requirements(question, enlarged) if row.citation_id}
            if not old_facets <= new_facets:
                record_rag_event(dict(kind="feedback", outcome="required_evidence_lost"))
                return result
            preflight(enlarged)
            guard.require()
            record_scope(question, scope)
            record_documents("feedback_evidence", merged)
            tick = time.perf_counter()
            try:
                candidate = regenerate(enlarged)
            finally:
                result.regeneration_ms = round((time.perf_counter() - tick) * 1000, 2)
            guard.require()
            refreshed = self.retrieval.refresh_evidence(merged, scope=scope, session=session)
            if context_signature(self.builder.build(refreshed, question=question)) != context_signature(enlarged):
                record_rag_event(dict(kind="feedback", outcome="evidence_changed_after_generation"))
                return result
            result.draft, result.documents, result.context = candidate, refreshed, enlarged
            result.regenerated = True
            record_rag_event(dict(kind="feedback", outcome="regenerated"))
            return result
        except (AuthorizationDenied, SQLAlchemyError):
            raise
        except RagBudgetExceeded:
            guard.require()
            record_rag_event(dict(kind="feedback", outcome="shared_budget"))
            return result
        except Exception as exc:
            guard.require()
            logger.warning("rag.feedback_failed error_type=%s", type(exc).__name__)
            record_rag_event(dict(kind="feedback", outcome="fallback", error_type=type(exc).__name__))
            return result
        finally:
            record_scope(question, scope)
