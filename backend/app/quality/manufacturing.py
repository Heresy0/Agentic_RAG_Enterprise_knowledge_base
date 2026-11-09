"""Manufacturing review bindings, separate from document UUID evidence."""
import json
from uuid import UUID

from sqlalchemy import select

from ..db.business_models import QARunBusinessScope
from ..manufacturing.answer_context import ManufacturingAnswerContext, business_citation, comparison_question, facts_only_question
from ..manufacturing.applicability import ApplicabilityResolver
from ..manufacturing.contracts import WorkOrderContext
from ..manufacturing.execution_contracts import context_from_payload
from ..manufacturing.evaluation_sources import registry_for
from ..manufacturing.service import public_applicability
from ..security.business_scope import BusinessAuthorizationService
from ..security.retrieval_scope import RetrievalScope
from .reviews import ReviewConflict


def business_rows(store, run):
    return list(store.session.scalars(select(QARunBusinessScope).where(
        QARunBusinessScope.tenant_id == run.tenant_id, QARunBusinessScope.run_id == run.id)))


def is_manufacturing(store, run):
    return bool(business_rows(store, run)) or run.snapshot_json.get("schema_version") == 3


def observed_context(store, run):
    try:
        data = run.snapshot_json["manufacturing"]
        if run.snapshot_json["schema_version"] != 3 or run.snapshot_status != "captured":
            raise ValueError()
        if data["validation"] != dict(source_verified=True, document_scope_verified=True,
                business_citations_verified=True, mes_reads=3):
            raise ValueError()
        context = context_from_payload(data["business_context"])
        rows = business_rows(store, run)
        if (context.record.tenant_id != run.tenant_id or len(rows) != 1 or
                (rows[0].source_system_id, rows[0].plant_code, rows[0].work_order_id) !=
                (context.record.source_system_id, context.record.plant_code, context.record.work_order_id)):
            raise ValueError()
        return context
    except (KeyError, TypeError, ValueError):
        raise ReviewConflict("制造快照或业务范围不完整，须重新采集后审核。") from None


def evidence_options(store, run):
    context = observed_context(store, run)
    return json.loads(json.dumps([business_citation(item, context)
        for item in ManufacturingAnswerContext(context, None).items], ensure_ascii=False))


def resolve_business_evidence(store, run, selected):
    if not is_manufacturing(store, run):
        if selected:
            raise ReviewConflict("普通样本不能附加工单字段证据。")
        return []
    if not selected:
        return []
    options = {row["citation_id"]: row for row in evidence_options(store, run)}
    if len({row.citation_id for row in selected}) != len(selected):
        raise ReviewConflict("业务字段证据不能重复。")
    result = []
    for chosen in selected:
        expected = options.get(chosen.citation_id)
        if expected is None or any(expected[key] != value for key, value in chosen.model_dump().items()):
            raise ReviewConflict("工单字段、值、来源身份、修订或哈希与当次快照不一致。")
        result.append(expected)
    return result


def current_binding(store, run, *, previous=None, verify_record=False):
    context = observed_context(store, run)
    try:
        registry = registry_for(store.settings)
        source = registry.check(store.session, store.principal,
            expected=previous["business_context_binding"]["source"] if previous else None,
            context=context if verify_record else None)
        business_scope = BusinessAuthorizationService(store.session).require_scope(principal=store.principal,
            source_system_id=context.record.source_system_id, permission="review")
        scope = RetrievalScope(tenant_id=run.tenant_id, knowledge_base_ids=frozenset(
            UUID(value) for value in store.scopes(run)))
        decision = ApplicabilityResolver().resolve(context=context, scope=scope,
            business_scope=business_scope, session=store.session,
            include_other_revisions=comparison_question(run.retrieval_question))
        original = run.snapshot_json["manufacturing"]
        applicability = dict(catalog_sha256=decision.scope.manufacturing.catalog_sha256,
            applicability=public_applicability(decision), rule_sources=original["rule_sources"])
        if (applicability["catalog_sha256"] != original["catalog_sha256"] or
                applicability["applicability"] != original["applicability"]):
            raise ValueError()
        result = dict(input_kind="manufacturing", business_context_binding=dict(
            context=context.model_dump(mode="json"), source=source), applicability_binding=applicability,
            business_scopes=[dict(source_system_id=context.record.source_system_id,
                plant_code=context.record.plant_code, work_order_id=context.record.work_order_id)])
        if previous is not None and any(previous.get(key) != value for key, value in result.items()):
            raise ValueError()
        return result
    except Exception:
        # Neither source URLs/credentials nor upstream response text may enter a review error.
        raise ReviewConflict("活跃工单夹具、来源或适用性已变化或不可验证，请重新采集并审核。") from None


def approval_evidence(run, payload, documents, business):
    if not payload.expected_answerable or facts_only_question(payload.evaluation_question):
        return
    fields = {row["field_name"] for row in business}
    if not {"work_order_id", "product_model", "operation_code", "actual_started_at"} <= fields:
        raise ReviewConflict("工单作业样本须选择编号、型号、工序、实际开工四项业务依据。")
    applicable = run.snapshot_json["manufacturing"]["applicability"]
    required = {row["document_id"] for row in applicable["documents"] if row["usage"] == "applicable_evidence"
        and row["document_role"] in ("instruction", "change_notice")}
    if not required <= {row["document_id"] for row in documents}:
        raise ReviewConflict("工单作业样本须选取决定适用版本的指导书和变更通知原文。")
