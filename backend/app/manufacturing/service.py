"""Three bounded MES reads, one shared RAG pipeline, atomic authorized delivery."""
from dataclasses import replace
from time import perf_counter

from starlette.concurrency import run_in_threadpool
from anyio import from_thread

from ..integrations.mes import MESContextChanged, MESError
from ..knowledge.answer_models import AnswerResult
from ..knowledge.conversations import HISTORY_TURNS
from ..security.authorization import AuthorizationService, AuthorizationDenied
from .access import WorkOrderAccessService
from .answer_context import (ManufacturingAnswerContext, facts_only_question, comparison_question,
                             manufacturing_answer_context, verified_business_citations, manufacturing_model_guard)
from .applicability import ApplicabilityResolver
from .conversations import ManufacturingConversationStore
from .history_access import invalidate_binding
from ..security.business_scope import BusinessAuthorizationDenied
from ..evaluation.trace import record_business_source

GAP_LABELS = {"product_model": "产品型号", "operation_code": "工序", "actual_started_at": "实际开工时间",
    "route_revision": "工艺路线修订", "change_notice_refs": "引用的书面通知", "manufacturing_review": "制造资料适用性审核",
    "source_anchors": "原文来源", "catalog_scope": "目录处理范围", "supersedes_rule_ids": "通知替代关系来源"}


def public_context(context):
    return {**context.record.model_dump(mode="json", exclude={"tenant_id", "schema_version"}),
        "fetched_at": context.fetched_at.isoformat(), "last_verified_at": context.last_verified_at.isoformat(),
        "payload_sha256": context.payload_sha256}


def public_applicability(decision):
    fence = decision.scope.manufacturing
    documents = (*fence.documents, *fence.explanation_documents)
    bindings = {row.document_id: row for row in documents}
    return dict(state=decision.state, reason=decision.reason, document_code=decision.selected_document_code,
        business_revision=decision.selected_business_revision, rule_ids=list(decision.rule_ids), gaps=list(decision.gaps),
        documents=[dict(document_id=str(row.document_id), knowledge_base_id=str(row.knowledge_base_id),
            document_version=row.document_version, document_content_hash=row.document_content_hash,
            manufacturing_revision=row.manufacturing_revision, document_code=row.document_code,
            business_revision=row.business_revision, document_role=row.document_role,
            usage=("conflict_evidence" if decision.state == "conflict" else "comparison_only"
                if row.document_role == "instruction" and row.business_revision != decision.selected_business_revision
                else "applicable_evidence")) for row in documents],
        evidence=[dict(document_id=str(row.document_id), chunk_id=row.anchor.chunk_id,
            parent_id=row.anchor.parent_id, quote_sha256=row.anchor.quote_sha256,
            knowledge_base_id=str(bindings[row.document_id].knowledge_base_id),
            document_version=bindings[row.document_id].document_version,
            document_name=bindings[row.document_id].document_code) for row in (*fence.required_evidence, *fence.explanation_evidence)])


class ManufacturingService:
    def __init__(self, *, session, principal, connector, answer_service, resolver=None):
        self.session, self.principal = session, principal
        self.access = WorkOrderAccessService(session, connector)
        self.answer_service = answer_service
        self.resolver = resolver

    def require_documents(self, scope):
        allowed = AuthorizationService(self.session).list_readable_knowledge_base_ids(principal=self.principal)
        if not scope.knowledge_base_ids or not scope.knowledge_base_ids <= allowed:
            raise AuthorizationDenied("知识库访问权限已变化，请重新查询。")

    async def answer(self, payload, *, scope, observation, commit_success, check_lease=lambda: None, frozen_input=None):
        started = perf_counter()
        timings = {}
        store = ManufacturingConversationStore(self.session, self.principal, scope)
        token = None; completed = False
        try:
            if payload.conversation_id is not None:
                if store.binding(payload.conversation_id).invalidated_at is not None:
                    raise MESContextChanged()
                token = store.acquire(payload.conversation_id)
            stage = perf_counter()
            context = await self.access.read_context(principal=self.principal, work_order_id=payload.work_order_id,
                expected_source_revision=payload.expected_source_revision)
            if frozen_input is not None:
                bound = frozen_input["business_context_binding"]["context"]
                if (context.payload_sha256 != bound["payload_sha256"] or context.record.model_dump(mode="json") != bound["record"]):
                    raise MESError()
                from .contracts import WorkOrderContext
                context = context.model_copy(update={"fetched_at": WorkOrderContext.model_validate(bound).fetched_at})
            timings["work_order_fetch_ms"] = round((perf_counter()-stage)*1000, 2)
            record_business_source(context, stage="fetched")
            if token:
                store.check_binding(payload.conversation_id, context)
            self.require_documents(scope); check_lease()
            def current_access():
                self.require_documents(scope)
                if not self.access.scope(self.principal).contains_record(context.record):
                    raise BusinessAuthorizationDenied()
                check_lease()
            question = payload.question.strip()
            stage = perf_counter()
            if token:
                state = store.context_memory(payload.conversation_id, token=token)
                store.check_binding(payload.conversation_id, context)
                turns = store.turns(payload.conversation_id, limit=HISTORY_TURNS, for_context=True)
                if self.resolver is not None and turns:
                    with manufacturing_model_guard(current_access):
                        question = await run_in_threadpool(self.resolver.resolve, question, turns)
                # Resolving a followup cannot replace trusted work-order facts.
                if not isinstance(question, str) or not question.strip() or len(question) > 2000:
                    raise ValueError("追问解析未形成有效问题，请写明本轮条件。")
            if observation is not None:
                observation.retrieval_question = question
            timings["conversation_context_ms"] = round((perf_counter()-stage)*1000, 2)
            facts_only = facts_only_question(question)
            comparison = comparison_question(question)
            stage = perf_counter()
            decision = ApplicabilityResolver().resolve(context=context, scope=scope,
                business_scope=self.access.scope(self.principal), session=self.session, include_other_revisions=comparison)
            if frozen_input is not None:
                from .scope import ManufacturingScopeChanged
                binding = frozen_input["applicability_binding"]
                if (decision.scope.manufacturing.catalog_sha256 != binding["catalog_sha256"] or
                        public_applicability(decision) != binding["applicability"]):
                    raise ManufacturingScopeChanged("冻结的制造适用性已变化。")
            if observation is not None:
                observation.scope = decision.scope
            timings["applicability_ms"] = round((perf_counter()-stage)*1000, 2)
            pre_verified = False
            async def before_generation():
                nonlocal context, pre_verified
                if pre_verified: return
                tick = perf_counter()
                context = await self.access.verify_context(principal=self.principal, previous=context)
                timings["pre_generation_verify_ms"] = round((perf_counter()-tick)*1000, 2)
                record_business_source(context, stage="pre_generation_verified")
                current_access(); decision.scope.require_current_manufacturing(self.session)
                pre_verified = True
            def model_access():
                current_access(); decision.scope.require_current_manufacturing(self.session)
            stage = perf_counter()
            if decision.state != "applicable" and not facts_only:
                await before_generation()
                clarify = decision.state == "needs_context"
                gaps = [GAP_LABELS.get(name, name) for name in decision.gaps]
                reason = ("请补齐或复核来源字段：" + "、".join(gaps) if clarify else
                          "当前可见资料存在未解决的选版冲突。" if decision.state == "conflict" else
                          "当前授权且已复核的资料不足，不能给出工单作业要求。")
                result = AnswerResult(answer=reason, answerable=False, citations=[], refusal_reason=None if clarify else reason,
                    response_type="clarify" if clarify else "refuse", missing_fields=gaps if clarify else [],
                    clarification_questions=["请先补齐来源记录或完成资料复核，再重新查询。"] if clarify else [])
                if observation is not None:
                    observation.context = dict(items=[], rendered_text=None, incomplete_reason=None)
            else:
                active = ManufacturingAnswerContext(context, decision, facts_only, comparison,
                    before_generation=lambda: from_thread.run(before_generation))
                with manufacturing_answer_context(active), manufacturing_model_guard(model_access):
                    result = await run_in_threadpool(self.answer_service.answer, question, scope=decision.scope, session=self.session)
                if not pre_verified:
                    raise MESError()
            timings["answer_pipeline_ms"] = round((perf_counter()-stage)*1000, 2)
            # Third read is mandatory even for a deterministic clarification.
            stage = perf_counter()
            context = await self.access.verify_context(principal=self.principal, previous=context)
            timings["pre_delivery_verify_ms"] = round((perf_counter()-stage)*1000, 2)
            record_business_source(context, stage="pre_delivery_verified")
            self.require_documents(scope); decision.scope.require_current_manufacturing(self.session); check_lease()
            if token:
                store.check_binding(payload.conversation_id, context)
                store.ensure_authorized(payload.conversation_id, token)
            business = verified_business_citations(result, context, decision)
            result = result.model_copy(update={"work_order_context": public_context(context),
                "applicability": public_applicability(decision), "business_citations": business,
                "manufacturing_timings": timings})
            snapshot = dict(schema_version=1, business_context=context.model_dump(mode="json"),
                applicability=public_applicability(decision), catalog_sha256=decision.scope.manufacturing.catalog_sha256,
                rule_sources=[dict(document_id=str(row.document_id), **row.anchor.model_dump(mode="json"))
                    for row in (*decision.scope.manufacturing.required_evidence, *decision.scope.manufacturing.explanation_evidence)],
                claim_bindings=result.claim_bindings, validation=dict(source_verified=True,
                    document_scope_verified=True, business_citations_verified=True, mes_reads=3))
            if token:
                store.append(payload.conversation_id, token, payload.question, question, result,
                    commit=False, expected_cutoff=state.cutoff)
            commit_success(result, decision.scope, snapshot)
            completed = True
            return result, round((perf_counter()-started)*1000, 2)
        except (MESContextChanged, MESError, AuthorizationDenied, BusinessAuthorizationDenied):
            if payload.conversation_id is not None:
                # A failed source read cannot leave historical context eligible
                # to resume silently after a revision or permission change.
                self.session.rollback()
                invalidate_binding(self.session, self.principal, payload.conversation_id)
            raise
        finally:
            if token and not completed:
                store.release(payload.conversation_id, token)
