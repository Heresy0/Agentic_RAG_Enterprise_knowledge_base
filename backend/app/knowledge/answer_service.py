import time
import json
from pydantic import ValidationError
from langchain_core.exceptions import OutputParserException

from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)
from langchain_openai import ChatOpenAI
from sqlalchemy.orm import Session

from .answer_models import (
    AnswerDraft,
    GenerationAnswerDraft,
    AnswerResult,
    AnswerTimings,
    Citation,
)
from .answer_validation import AnswerValidator
from .calculation_service import CalculationError, compute
from ..config import Settings
from ..model_usage import audited_scope, invoke_structured
from .context_builder import (
    BuiltContext,
    ContextBuilder,
)
from .prompts import ANSWER_SYSTEM_PROMPT
from .answer_constraints import complete_cited_table_conditions, generation_constraints, temporal_evidence_context
from .answer_completion import complete_answer
from .answer_coverage import coverage_errors, coverage_snapshot, requirement_prompt
from .answer_contract import answer_contract, direct_answer_errors
from .temporal import future_certainty_gap
from .retrieval_service import RetrievalService
from .evidence_coordinator import EvidenceCoordinator
from .evidence_feedback import EvidenceFeedback
from .query_planner import QueryPlanner, query_plan_scope
from .answer_repair import AnswerRepair
from .rag_budget import RagBudget, rag_budget_scope, current_rag_budget
from ..security.retrieval_scope import RetrievalScope
from ..evaluation.trace import (record_context, record_documents, record_scope, record_calculations,
                                record_answer_decision, record_answer_completion, record_answer_generation, record_rag_event)


REFUSAL_TEXT = (
    "根据当前知识库资料，暂时无法回答这个问题。"
)


def _elapsed_ms(started: float) -> float:
    return round(
        (time.perf_counter() - started) * 1000,
        2,
    )


class AnswerService:
    def __init__(
        self,
        *,
        settings: Settings,
        retrieval_service: RetrievalService,
        context_builder: ContextBuilder | None = None,
        validator: AnswerValidator | None = None,
        evidence_coordinator: EvidenceCoordinator | None = None,
        query_planner: QueryPlanner | None = None,
        answer_repair: AnswerRepair | None = None,
        evidence_feedback: EvidenceFeedback | None = None,
        regeneration_model_factory=None,
    ) -> None:
        self._settings = settings
        self._retrieval_service = retrieval_service
        self._evidence_coordinator = evidence_coordinator or EvidenceCoordinator(settings, retrieval_service)
        self._query_planner = query_planner or QueryPlanner(settings)
        self._answer_repair = answer_repair or AnswerRepair(settings)
        self._context_builder = (
            context_builder or ContextBuilder()
        )
        self._validator = (
            validator or AnswerValidator()
        )
        self._evidence_feedback = evidence_feedback or EvidenceFeedback(settings, retrieval_service,
            self._evidence_coordinator, self._context_builder)
        self._regeneration_model_factory = regeneration_model_factory or ChatOpenAI

        llm = ChatOpenAI(
            model=settings.chat_model,
            base_url=settings.chat_base_url,
            api_key=settings.chat_api_key,
            temperature=0,
            reasoning_effort="none",
            timeout=60,
            max_retries=0,
        )

        self._structured_llm = (
            llm.with_structured_output(GenerationAnswerDraft, include_raw=True)
        )

    @audited_scope
    def answer(
        self,
        question: str,
        *,
        scope: RetrievalScope,
        session: Session,
    ) -> AnswerResult:
        question = question.strip()

        if not question:
            raise ValueError("问题不能为空")
        total_started = time.perf_counter()
        with rag_budget_scope(RagBudget.from_settings(self._settings)) as budget:
            try:
                planning_started = time.perf_counter()
                plan = self._query_planner.plan(question, scope=scope, session=session)
                planning_ms = _elapsed_ms(planning_started) if plan is not None else None
                with query_plan_scope(plan):
                    return self._answer(question, scope=scope, session=session, plan=plan,
                                        planning_ms=planning_ms, total_started=total_started)
            finally:
                record_rag_event(dict(kind="budget", **budget.snapshot()))

    def _answer(self, question, *, scope, session, plan, planning_ms, total_started):
        record_scope(question, scope)
        timings: dict[str, float | None] = {
            "query_planning_ms": planning_ms,
            "hybrid_retrieval_ms": None,
            "rerank_ms": None,
            "search_total_ms": 0.0,
            "context_build_ms": 0.0,
            "generation_ms": None,
            "validation_ms": None,
            "render_ms": None,
        }

        search_started = time.perf_counter()
        collected = self._evidence_coordinator.collect(
            question,
            scope=scope,
            session=session,
            **({"plan": plan} if plan is not None else {}),
        )
        documents = collected.documents
        timings["agent_decision_ms"] = collected.decision_ms
        timings["agent_tool_ms"] = collected.tool_ms
        # 防御性校验：缺少来源范围或越权的分块不得进入上下文/生成模型。
        documents = [document for document in documents if scope.contains(
            document.metadata.get("tenant_id"), document.metadata.get("knowledge_base_id"),
        )]
        record_documents("authorized_final", documents)
        timings["search_total_ms"] = _elapsed_ms(
            search_started
        )

        if documents:
            metadata = documents[0].metadata
            timings["hybrid_retrieval_ms"] = (
                self._metadata_latency(
                    metadata,
                    "retrieval_latency_ms",
                )
            )
            timings["rerank_ms"] = self._metadata_latency(
                metadata,
                "rerank_latency_ms",
            )

        context_started = time.perf_counter()
        context = self._context_builder.build(
            documents, question=question,
        )
        record_context(context)
        timings["context_build_ms"] = _elapsed_ms(
            context_started
        )

        future_gap = bool(context.items) and future_certainty_gap(question, context.items)
        if not context.items or future_gap:
            render_started = time.perf_counter()
            result = self._refusal(
                "缺少已确定覆盖该未来期间的正式制度，不能把现行规则说成未来承诺。"
                if future_gap else "没有检索到相关知识库资料。"
            )
            record_answer_decision(stage="pre_generation", answerable=False,
                                   reason="future_policy_gap" if future_gap else "empty_context")
            timings["render_ms"] = _elapsed_ms(
                render_started
            )
            return self._finish_result(
                result=result,
                timings=timings,
                total_started=total_started,
            )

        def refresh_context():
            current = self._retrieval_service.refresh_evidence(documents, scope=scope, session=session)
            refreshed = self._context_builder.build(current, question=question)
            return [(item.chunk_id, item.content, item.policy) for item in refreshed.items] == [
                (item.chunk_id, item.content, item.policy) for item in context.items]

        generation_started = time.perf_counter()
        schema_recovered = False
        try:
            draft = self._generate_draft(question=question, context=context)
        except (ValidationError, OutputParserException) as exc:
            if not getattr(self._settings, 'rag_repair_enabled', False):
                raise
            errors = ([dict(location=[str(part) for part in error['loc']], type=error['type'])
                       for error in exc.errors(include_input=False, include_context=False, include_url=False)[:12]]
                      if isinstance(exc, ValidationError) else [])
            record_rag_event(dict(kind='schema_error', error_type=type(exc).__name__, errors=errors))
            record_answer_generation(self._refusal('首次生成未形成结构化草稿。'), [],
                dict(requirements=json.loads(requirement_prompt(question, context)), coverage=[],
                     errors=['结构化输出解析失败'], structurally_complete=False),
                changed=False, validation_errors=['结构化输出解析失败'])
            schema_started = time.perf_counter()
            draft = self._answer_repair.recover_schema(messages=self._generation_messages(question, context),
                scope=scope, session=session, refresh_context=refresh_context)
            timings['answer_repair_ms'] = _elapsed_ms(schema_started)
            schema_recovered = True
            if draft is None:
                timings['generation_ms'] = _elapsed_ms(generation_started)
                record_answer_decision(stage='generation_error', answerable=False, reason='structured_output_invalid')
                return self._finish_result(result=self._refusal('模型输出格式异常，未形成可验证答案，请重试。'),
                                           timings=timings, total_started=total_started)
        timings["generation_ms"] = _elapsed_ms(
            generation_started
        )
        record_answer_decision(stage="generation", answerable=draft.answerable,
                               reason=draft.refusal_reason,
                               temporal=temporal_evidence_context(question, context.items))

        regenerated = False
        original_draft, original_context = draft, context
        feedback = self._evidence_feedback.run(draft, question=question, documents=documents,
            context=context, scope=scope, session=session,
            preflight=lambda candidate_context: self._regeneration_preflight(question, candidate_context),
            regenerate=lambda candidate_context: self._regenerate_draft(question=question, context=candidate_context))
        if feedback is not None:
            timings["feedback_search_ms"] = feedback.search_ms
            timings["answer_regeneration_ms"] = feedback.regeneration_ms
            if feedback.search_ms is not None:
                timings["search_total_ms"] += feedback.search_ms
            if feedback.regenerated:
                record_context(original_context, stage="initial_context")
                record_documents("initial_authorized", documents)
                record_answer_generation(self._refusal(original_draft.refusal_reason or "知识库资料不足。"),
                    [claim.model_dump() for claim in original_draft.claims],
                    coverage_snapshot(original_draft, question, original_context), changed=False,
                    validation_errors=self._validator.validate(draft=original_draft,
                        context=original_context, question=question).errors,
                    stage='answer_schema_recovery' if schema_recovered else 'answer_generation')
                draft, documents, context = feedback.draft, feedback.documents, feedback.context
                regenerated = True
                record_documents("authorized_final", documents)
                record_context(context)
                record_answer_decision(stage="regeneration", answerable=draft.answerable,
                    reason=draft.refusal_reason, temporal=temporal_evidence_context(question, context.items))

        validation_started = time.perf_counter()
        # Coverage is checked against the original draft, before any supplement.
        # A fallback must not turn an explicit missing requirement into a success.
        coverage_failures = coverage_errors(draft, question, context)
        calculation_records = []
        for claim in draft.claims:
            if claim.calculation is None:
                continue
            try:
                calculation = compute(claim, context, question)
                claim.text = calculation.rendered_text
                calculation_records.append(dict(operation=claim.calculation.operation, status="verified",
                                                result=calculation.result, inputs=list(calculation.inputs),
                                                proposed_result=claim.calculation.result,
                                                model_result_matches=calculation.model_result_matches,
                                                corrected_model_result=calculation.model_result_matches is False))
            except CalculationError as exc:
                calculation_records.append(dict(operation=claim.calculation.operation, status="rejected", reason=str(exc),
                                                proposed=claim.calculation.model_dump()))
        record_calculations(calculation_records)
        first_validation = self._validator.validate(draft=draft, context=context, question=question)
        first_contract_errors = direct_answer_errors(question, draft)
        first_response = (self._render_result(draft=draft, context=context)
                          if first_validation.valid and draft.answerable and not coverage_failures and not first_contract_errors
                          else self._refusal(draft.refusal_reason or "首次生成未通过证据与必答项校验。"))
        first_claims = [claim.model_dump() for claim in draft.claims]
        first_answerable = draft.answerable
        first_coverage = coverage_snapshot(draft, question, context)
        first_errors = list(dict.fromkeys([*coverage_failures, *first_validation.errors, *first_contract_errors]))
        repair_started = time.perf_counter()
        repaired = (draft if schema_recovered else self._answer_repair.repair(draft,
            question=question, context=context, validator=self._validator, scope=scope,
            session=session, refresh_context=refresh_context))
        if repaired != draft:
            draft = repaired
            coverage_failures = coverage_errors(draft, question, context)
        if getattr(self._settings, "rag_repair_enabled", False) and first_errors:
            timings["answer_repair_ms"] = _elapsed_ms(repair_started)
        decisions = []
        if not coverage_failures:
            before_conditions = [claim.model_dump() for claim in draft.claims]
            complete_cited_table_conditions(question, draft, context)
            if before_conditions != [claim.model_dump() for claim in draft.claims] or draft.answerable != first_answerable:
                decisions.append(dict(kind="table_conditions", status="appended" if draft.answerable else "refused_conflict"))
            decisions.extend(complete_answer(question, draft, context))
        record_answer_completion(decisions)
        record_answer_generation(
            first_response, first_claims, first_coverage,
            changed=first_claims != [claim.model_dump() for claim in draft.claims] or first_answerable != draft.answerable,
            validation_errors=first_errors,
            stage=("answer_regeneration" if regenerated else
                   "answer_schema_recovery" if schema_recovered else "answer_generation"),
        )
        # Coverage describes first-pass claims. Existing citation/calculation checks
        # validate the final supplement too, without relabelling it as first-pass coverage.
        validation = self._validator.validate(
            draft=AnswerDraft.model_validate(draft.model_dump(exclude={"coverage"})),
            context=context,
            question=question,
        )
        if coverage_failures:
            validation.valid = False
            validation.errors.extend(coverage_failures)
        contract_failures = direct_answer_errors(question, draft)
        if contract_failures:
            validation.valid = False
            validation.errors.extend(contract_failures)
        timings["validation_ms"] = _elapsed_ms(
            validation_started
        )

        if not validation.valid:
            render_started = time.perf_counter()
            result = self._refusal(
                "生成结果未通过证据与引用校验："
                + "；".join(validation.errors)
            )
            record_answer_decision(stage="validation", answerable=False, reason="evidence_validation_failed")
            if regenerated:
                record_rag_event(dict(kind='feedback_final', answerable=False))
            timings["render_ms"] = _elapsed_ms(
                render_started
            )
            return self._finish_result(
                result=result,
                timings=timings,
                total_started=total_started,
            )

        if not draft.answerable:
            render_started = time.perf_counter()
            result = self._refusal(
                draft.refusal_reason
                or "知识库资料不足。"
            )
            record_answer_decision(stage="final", answerable=False, reason=draft.refusal_reason)
            if regenerated:
                record_rag_event(dict(kind='feedback_final', answerable=False))
            timings["render_ms"] = _elapsed_ms(
                render_started
            )
            return self._finish_result(
                result=result,
                timings=timings,
                total_started=total_started,
            )

        render_started = time.perf_counter()
        result = self._render_result(
            draft=draft,
            context=context,
        )
        record_answer_decision(stage="final", answerable=True)
        if regenerated:
            record_rag_event(dict(kind='feedback_final', answerable=True))
        timings["render_ms"] = _elapsed_ms(
            render_started
        )

        return self._finish_result(
            result=result,
            timings=timings,
            total_started=total_started,
        )

    @staticmethod
    def _metadata_latency(
        metadata: dict,
        key: str,
    ) -> float | None:
        value = metadata.get(key)

        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        ):
            return round(float(value), 2)

        return None

    @staticmethod
    def _finish_result(
        *,
        result: AnswerResult,
        timings: dict[str, float | None],
        total_started: float,
    ) -> AnswerResult:
        completed_timings = dict(timings)
        completed_timings["total_ms"] = _elapsed_ms(
            total_started
        )

        return result.model_copy(
            update={
                "timings": AnswerTimings(
                    **completed_timings
                ),
            }
        )

    def _generate_draft(
        self,
        *,
        question: str,
        context: BuiltContext,
    ) -> GenerationAnswerDraft:
        result = invoke_structured(self._structured_llm, self._generation_messages(question, context),
                                   stage="answer_generation", model_name=self._settings.chat_model)
        return self._as_generation_draft(result)

    def _generation_messages(self, question, context):
        requirements = requirement_prompt(question, context)
        # Only server-selected fixed guidance and compiler-owned IDs go into
        # the system message. Original questions and source text remain data.
        contract = answer_contract(question, json.loads(requirements))
        return [
            SystemMessage(
                content=ANSWER_SYSTEM_PROMPT + "\n本次回答要求（不改变必答项、权限与最终校验）：\n" + contract
            ),
            HumanMessage(
                content=(
                    f"用户问题：\n{question}\n\n"
                    f"必答问题清单（用户数据）：\n{requirements}\n\n"
                    f"作答检查：\n{generation_constraints(question, context)}\n\n"
                    f"知识库资料：\n{context.text}"
                )
            ),
        ]

    def _regeneration_preflight(self, question, context):
        messages = self._generation_messages(question, context)
        prompt_bytes = sum(len(str(message.content).encode("utf-8")) for message in messages)
        prompt_bytes += len(json.dumps(GenerationAnswerDraft.model_json_schema()).encode("utf-8")) + 256
        output = getattr(self._settings, "rag_regeneration_output_tokens", 2048)
        current_rag_budget().check_model(prompt_bytes=prompt_bytes, output_tokens=output)
        return messages, prompt_bytes, output

    def _regenerate_draft(self, *, question, context):
        messages, prompt_bytes, output = self._regeneration_preflight(question, context)
        with current_rag_budget().model_attempt(prompt_bytes=prompt_bytes, output_tokens=output,
                                                purpose="regeneration") as remaining:
            model = self._regeneration_model_factory(model=self._settings.chat_model,
                base_url=self._settings.chat_base_url, api_key=self._settings.chat_api_key, temperature=0,
                reasoning_effort="none", max_retries=0, max_tokens=output,
                timeout=min(getattr(self._settings, "rag_regeneration_timeout_seconds", 15), remaining)
            ).with_structured_output(GenerationAnswerDraft, include_raw=True)
            result = invoke_structured(model, messages, stage="answer_regeneration", model_name=self._settings.chat_model)
            return self._as_generation_draft(result)

    @staticmethod
    def _as_generation_draft(result):
        if not isinstance(result, GenerationAnswerDraft):
            if isinstance(result, AnswerDraft):
                result = result.model_dump()
            result = GenerationAnswerDraft.model_validate(result)

        return result

    def _render_result(
        self,
        *,
        draft: AnswerDraft | GenerationAnswerDraft,
        context: BuiltContext,
    ) -> AnswerResult:
        item_map = {
            item.citation_id: item
            for item in context.items
        }

        answer_parts: list[str] = []
        used_citations: list[str] = []

        for claim in draft.claims:
            citation_text = "".join(
                f"[{citation_id}]"
                for citation_id in claim.citations
            )

            answer_parts.append(
                f"{claim.text.rstrip('。')}。"
                f"{citation_text}"
            )

            for citation_id in claim.citations:
                if citation_id not in used_citations:
                    used_citations.append(citation_id)

        citations = []

        for citation_id in used_citations:
            item = item_map[citation_id]
            citations.append(
                Citation(
                    citation_id=item.citation_id,
                    document_id=item.document_id,
                    document_name=item.document_name,
                    section_path=item.section_path,
                    chunk_id=item.chunk_id,
                    parent_id=item.parent_id,
                    knowledge_base_id=item.knowledge_base_id,
                    knowledge_base_name=item.knowledge_base_name,
                )
            )

        return AnswerResult(
            answer="\n".join(answer_parts),
            answerable=True,
            citations=citations,
        )

    @staticmethod
    def _refusal(reason: str) -> AnswerResult:
        return AnswerResult(
            answer=REFUSAL_TEXT,
            answerable=False,
            citations=[],
            refusal_reason=reason,
        )
