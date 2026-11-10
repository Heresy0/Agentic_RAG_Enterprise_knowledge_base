"""Deterministic instruction selection over authorized, reviewed SQL sources.

No vector top-k, filename dates, report dates, gold labels or model guesses enter
the decision. Unknown and conflicting conditions yield an empty retrieval fence.
"""
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from ..documents.publication import is_published
from ..evaluation.trace import record_manufacturing_selection
from ..security.business_scope import BusinessAuthorizationDenied, BusinessScope
from ..security.retrieval_scope import RetrievalScope
from .contracts import WorkOrderContext, source_time
from .metadata import ManufacturingMetadata, current_review, validate_anchors
from .scope import (DocumentBinding, ManufacturingFence, RequiredEvidence,
                    catalog_fingerprint, catalog_rows, matches_record as _matches)


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    binding: DocumentBinding
    metadata: ManufacturingMetadata


@dataclass(frozen=True, slots=True)
class ApplicabilityDecision:
    state: str
    reason: str
    scope: RetrievalScope
    selected_document_code: str | None = None
    selected_business_revision: str | None = None
    rule_ids: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()


class ApplicabilityResolver:
    def resolve(self, *, context: WorkOrderContext, scope: RetrievalScope,
                business_scope: BusinessScope, session, include_other_revisions=False) -> ApplicabilityDecision:
        record = context.record
        if scope.manufacturing is not None:
            raise ValueError("适用性判断必须从知识库授权范围开始。")
        if (scope.tenant_id != record.tenant_id or business_scope.tenant_id != scope.tenant_id
                or not business_scope.contains_record(record)):
            raise BusinessAuthorizationDenied()
        if record.schema_version=='openmes-work-order-v1':
            from .step_guidance import resolve_step_guidance
            decision=resolve_step_guidance(context,scope,session,include_other_revisions=include_other_revisions)
            record_manufacturing_selection(decision)
            return decision
        rows = catalog_rows(session, scope, record=record)
        fingerprint = catalog_fingerprint(rows)

        def finish(state, reason, *, selected=None, rules=(), documents=(), required=(), gaps=()):
            explanation_docs, explanation = (), ()
            if state == "conflict" and rules:
                explanation_docs = tuple({entry.binding.document_id: entry.binding for entry, _ in rules}.values())
                explanation = tuple(RequiredEvidence(entry.binding.document_id, rule.anchor) for entry, rule in rules)
                if len(explanation_docs) > 32 or len(explanation) > 24:
                    state, reason, gaps = "needs_context", "evidence_budget_exceeded", ("catalog_scope",)
                    explanation_docs, explanation = (), ()
            fence = ManufacturingFence(state, fingerprint, tuple(documents), tuple(required), explanation_docs,
                                       explanation, catalog_record=record)
            decision = ApplicabilityDecision(state, reason, replace(scope, manufacturing=fence),
                selected.metadata.document_code if selected else None,
                selected.metadata.business_revision if selected else None,
                tuple(rule.rule_id for _, rule in rules), tuple(gaps))
            record_manufacturing_selection(decision)
            return decision

        missing = tuple(name for name in ("product_model", "operation_code", "actual_started_at")
                        if not getattr(record, name))
        if missing:
            return finish("needs_context", "missing_work_order_fields", gaps=missing)
        entries = []
        for doc in rows:
            if doc.status != "ready" or not is_published(doc):
                continue
            review = current_review(doc)
            if review is None:
                return finish("needs_context", "published_metadata_review_invalid", gaps=("manufacturing_review",))
            try:
                validate_anchors(session, doc, review.data)
            except ValueError:
                if _matches(review.data, record, ignore_route=True):
                    return finish("needs_context", "rule_source_stale", gaps=("source_anchors",))
                continue
            entries.append(CatalogEntry(DocumentBinding.reviewed(doc, review), review.data))
        if record.route_revision is None and any(entry.metadata.route_revisions
                and _matches(entry.metadata, record, ignore_route=True) for entry in entries):
            return finish("needs_context", "missing_route_revision", gaps=("route_revision",))
        matching = [entry for entry in entries if _matches(entry.metadata, record)]
        instructions = [entry for entry in matching if entry.metadata.document_role == "instruction"]
        if not instructions:
            return finish("no_evidence", "no_reviewed_instruction")
        families = {(entry.metadata.document_family_id, entry.metadata.document_code) for entry in instructions}
        if len(families) != 1:
            return finish("conflict", "multiple_instruction_families")
        _, code = next(iter(families))
        notices = [entry for entry in matching if entry.metadata.document_role == "change_notice"]
        known_refs = {entry.metadata.document_code for entry in notices}
        if set(record.change_notice_refs) - known_refs:
            return finish("needs_context", "referenced_notice_unavailable", gaps=("change_notice_refs",))
        def effective_notice(entry):
            data = entry.metadata
            as_of = context.last_verified_at.astimezone(ZoneInfo(data.business_timezone)).date()
            return (data.notice_state == "active" and (data.effective_from is None or data.effective_from <= as_of)
                    and (data.effective_to is None or data.effective_to >= as_of))
        rules = [(entry, rule) for entry in notices if effective_notice(entry)
                 for rule in entry.metadata.rules if rule.target_document_code == code
                 and rule.product_model == record.product_model and rule.operation_code == record.operation_code]
        if len(rules) > 64:
            return finish("needs_context", "rule_budget_exceeded", gaps=("catalog_scope",))
        ids = [rule.rule_id for _, rule in rules]
        if len(ids) != len(set(ids)):
            return finish("conflict", "duplicate_rule_ids")
        baseline, overrides = [], []
        started = source_time(record.actual_started_at, record.actual_start_precision)
        for entry, rule in rules:
            tz = ZoneInfo(entry.metadata.business_timezone)
            start_date = started.astimezone(tz).date() if isinstance(started, datetime) else started
            if rule.kind == "work_order_override":
                if record.work_order_id in rule.work_order_ids and rule.effective_from <= context.last_verified_at.astimezone(tz).date():
                    overrides.append((entry, rule))
            elif ((rule.kind == "start_before" and start_date < rule.cutoff_date)
                    or (rule.kind == "start_on_or_after" and start_date >= rule.cutoff_date)):
                baseline.append((entry, rule))
        applicable = overrides or baseline
        if overrides:
            by_id = {rule.rule_id: rule for _, rule in overrides}
            edges = {key: set(rule.supersedes_rule_ids) & by_id.keys() for key, rule in by_id.items()}
            if any(set(rule.supersedes_rule_ids) - set(ids) for rule in by_id.values()):
                return finish("needs_context", "superseded_rule_unavailable", gaps=("supersedes_rule_ids",))
            visiting, visited = set(), set()
            def cyclic(key):
                if key in visiting:
                    return True
                if key in visited:
                    return False
                visiting.add(key)
                if any(cyclic(target) for target in edges.get(key, ())):
                    return True
                visiting.remove(key)
                visited.add(key)
                return False
            if any(cyclic(key) for key in edges):
                return finish("conflict", "supersession_cycle")
            replaced_ids = set().union(*edges.values())
            applicable = [(entry, rule) for entry, rule in overrides if rule.rule_id not in replaced_ids]
        if not applicable:
            return finish("no_evidence", "no_reviewed_start_rule")
        revisions = {rule.target_business_revision for _, rule in applicable}
        if len(revisions) != 1:
            return finish("conflict", "incompatible_written_rules", rules=applicable)
        revision = next(iter(revisions))
        selected = [entry for entry in instructions if entry.metadata.business_revision == revision]
        if len(selected) != 1:
            return finish("conflict" if selected else "no_evidence", "instruction_identity_unresolved", rules=applicable)
        selected = selected[0]
        # Instruction expiry cannot erase a source-authorized in-process carryover.
        # Other references use the trusted context verification date in their own
        # reviewed business timezone, never a date extracted from the question.
        supporting = []
        for entry in matching:
            data = entry.metadata
            as_of = context.last_verified_at.astimezone(ZoneInfo(data.business_timezone)).date()
            if data.document_role in ("instruction", "change_notice"):
                continue
            if ((data.effective_from is None or data.effective_from <= as_of)
                    and (data.effective_to is None or data.effective_to >= as_of)):
                supporting.append(entry)
        # An overlapping specification is a review conflict, not permission to
        # mix limits from two revisions or select the newest filename.
        identities = [(entry.metadata.document_role, entry.metadata.document_family_id, entry.metadata.document_code)
                      for entry in supporting if entry.metadata.document_role in ("inspection", "reference", "route")]
        if len(identities) != len(set(identities)):
            return finish("conflict", "overlapping_supporting_documents")
        deciding = tuple({(entry.binding.document_id, rule.rule_id): (entry, rule)
                          for entry, rule in (*baseline, *overrides)}.values())
        allowed = {entry.binding.document_id: entry for entry in
                   (selected, *supporting, *(entry for entry, _ in deciding))}
        required = [RequiredEvidence(selected.binding.document_id, anchor) for anchor in selected.metadata.source_anchors]
        if include_other_revisions:
            for entry in instructions:
                allowed[entry.binding.document_id] = entry
                required.extend(RequiredEvidence(entry.binding.document_id, anchor) for anchor in entry.metadata.source_anchors)
        required.extend(RequiredEvidence(entry.binding.document_id, rule.anchor) for entry, rule in deciding)
        required.extend(RequiredEvidence(entry.binding.document_id, anchor) for entry in supporting
                        if entry.metadata.document_role == "inspection" for anchor in entry.metadata.source_anchors)
        required = tuple({(row.document_id, row.anchor.model_dump_json()): row for row in required}.values())
        if len(allowed) > 32 or len(required) > 24:
            return finish("needs_context", "evidence_budget_exceeded", gaps=("catalog_scope",))
        return finish("applicable", "written_work_order_override" if overrides else "actual_start_window",
            selected=selected, rules=deciding, documents=tuple(entry.binding for entry in allowed.values()), required=required)
