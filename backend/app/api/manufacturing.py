"""Authenticated, read-only work-order guidance over the shared QA services."""
from contextlib import contextmanager
import logging
from pathlib import Path
import traceback
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..audit_service import AuditService
from ..db.dependencies import get_database_session
from ..db.quality_models import QARun
from sqlalchemy import select
from ..governance.tenant_rate_limiter import RateLimiterUnavailable, TenantRateLimitExceeded
from ..governance.tenant_concurrency_limiter import (ConcurrencyLimiterUnavailable, TenantConcurrencyExceeded,
    maintain_concurrency_lease)
from ..integrations.mes import MESError, MESNotFound, MESUnavailable
from ..knowledge.answer_models import AnswerResult
from ..knowledge.generation_errors import RECOVERABLE_GENERATION_ERRORS
from ..knowledge.milvus_store import VectorStoreUnavailable
from ..knowledge.conversations import ConversationBusy, ConversationNotFound
from ..manufacturing.access import WorkOrderAccessService
from ..manufacturing.batch_access import BatchExecutionAccess, public_execution
from ..manufacturing.contracts import Contract, Identifier, ManufacturingQuestion
from ..manufacturing.execution_contracts import ExecutionSelection, ManufacturingExecutionQuestion, selection_kwargs
from ..manufacturing.errors import ManufacturingInputError
from ..manufacturing.conversations import ManufacturingConversationStore
from ..manufacturing.history_access import invalidate_binding, invalidate_revoked_conversations, invalidate_scope_bindings
from ..manufacturing.scope import ManufacturingScopeChanged
from ..manufacturing.service import ManufacturingService, public_context
from ..model_usage import usage_context
from ..monitoring.observability import event_message
from ..quality.capture import capture_snapshot
from ..quality.eval_budget import EvaluationStopped
from ..quality.service import save_run
from ..security.authorization import AuthorizationDenied, AuthorizationService
from ..security.business_scope import BusinessAuthorizationDenied
from ..security.dependencies import get_current_principal
from ..security.principal import Principal
from .qa import rate_limit_headers, concurrency_headers

router = APIRouter(prefix="/api/manufacturing", tags=["manufacturing"])
logger = logging.getLogger(__name__)


class ConversationInput(ExecutionSelection):
    work_order_id: Identifier
    knowledge_base_id: UUID | None = None
    expected_source_revision: Identifier | None = None


class ManufacturingResponse(AnswerResult):
    work_order_context: dict
    applicability: dict
    business_citations: list[dict] = Field(default_factory=list)
    claim_bindings: list[dict] = Field(default_factory=list)
    run_id: UUID | None = None
    snapshot_status: str
    conversation_id: UUID | None = None
    request_id: str
    manufacturing_total_ms: float


def connector_for(request, principal):
    connector = getattr(request.app.state, "mes_connectors", {}).get(principal.tenant_id)
    if connector is None:
        raise MESUnavailable()
    return connector


def fail(exc, request_id):
    headers = {"Cache-Control": "no-store", "X-Request-ID": request_id}
    if isinstance(exc, BusinessAuthorizationDenied): exc = MESNotFound()
    if isinstance(exc, MESError):
        code, name, message = exc.status_code, exc.error_code, exc.message
    elif isinstance(exc, ManufacturingScopeChanged):
        code, name, message = 409, "manufacturing_scope_changed", "制造资料已变化，请重新查询适用性。"
    elif isinstance(exc, ConversationNotFound):
        code, name, message = 404, "conversation_unavailable", "对话不存在或不可访问。"
    elif isinstance(exc, ConversationBusy):
        code, name, message = 409, "conversation_busy", "对话正在使用，请稍后重试。"
    elif isinstance(exc, AuthorizationDenied):
        code, name, message = 403, "document_access_changed", "知识库权限不足或已变化。"
    elif isinstance(exc, TenantRateLimitExceeded):
        code, name, message = 429, "rate_limited", "请求过于频繁，请稍后重试。"
        headers.update(rate_limit_headers(exc.decision)); headers["Retry-After"] = str(exc.decision.reset_after_seconds)
    elif isinstance(exc, TenantConcurrencyExceeded):
        code, name, message = 429, "concurrency_limited", "当前问答并发额度已满，请稍后重试。"
        headers.update(concurrency_headers(exc.decision)); headers["Retry-After"] = str(max(1, exc.decision.retry_after_seconds))
    elif isinstance(exc, (RateLimiterUnavailable, ConcurrencyLimiterUnavailable)):
        code, name, message = 503, "governance_unavailable", "请求治理服务暂不可用。"
    elif isinstance(exc, ValidationError):
        code, name, message = 503, "manufacturing_generation_invalid", "生成结果未形成可验证的回答，请重新查询。"
    elif isinstance(exc, ManufacturingInputError):
        code, name, message = 422, "manufacturing_input_invalid", "请求或追问条件无法验证，请重新查询。"
    elif isinstance(exc, EvaluationStopped):
        code, name, message = 503, "manufacturing_evaluation_stopped", "本次验证已停止，请检查任务状态。"
    elif isinstance(exc, VectorStoreUnavailable):
        code, name, message = 503, "manufacturing_retrieval_unavailable", "检索服务暂不可用，请稍后重新查询。"
    elif isinstance(exc, RECOVERABLE_GENERATION_ERRORS):
        code, name, message = 503, "manufacturing_generation_unavailable", "生成服务暂不可用，请稍后重新查询。"
    elif isinstance(exc, SQLAlchemyError):
        code, name, message = 503, "manufacturing_storage_unavailable", "业务数据服务暂不可用，请稍后重新查询。"
    else:
        log_internal_error(exc, request_id)
        code, name, message = 500, "manufacturing_internal_error", "服务处理异常，请凭请求编号联系管理员。"
    return HTTPException(code, dict(error_code=name, message=message, request_id=request_id), headers=headers)


def log_internal_error(exc, request_id, *, stage="request"):
    # Exception messages, source lines, locals and chained provider payloads can
    # contain credentials. Log code locations and type only, tied to the request.
    frames = [dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
              for frame in traceback.extract_tb(exc.__traceback__)[-8:]]
    logger.error(event_message("manufacturing.internal_error", request_id=request_id,
                              stage=stage, error_type=type(exc).__name__, frames=frames))


@contextmanager
def manufacturing_operation(session, request_id, *, failed=None):
    """One HTTP error boundary; inner layers propagate typed failures."""
    try:
        yield
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        if failed is not None:
            failed(exc)
        raise fail(exc, request_id) from None


@router.get("/work-orders")
async def work_orders(request: Request, response: Response, limit: int = Query(20, ge=1, le=20),
        cursor: str | None = Query(None, min_length=1, max_length=1024),
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier = uuid4().hex; response.headers.update({"Cache-Control": "no-store", "X-Request-ID": identifier})
    with manufacturing_operation(session, identifier):
        connector = connector_for(request, principal)
        page = await WorkOrderAccessService(session, connector).list_work_orders(
            principal=principal, cursor=cursor, limit=limit)
        return dict(items=[row.model_dump(mode="json", exclude={"tenant_id", "schema_version"}) for row in page.items],
                    next_cursor=page.next_cursor, **({'selection_supported': True} if connector.config.execution_selection else {}),
                    **({'execution_kind': 'batch-step'} if connector.config.batch_step_selection else {}))


@router.get('/work-orders/{work_order_id}/batches')
async def work_order_batches(work_order_id: Identifier, request: Request, response: Response,
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier=uuid4().hex; response.headers.update({'Cache-Control':'no-store','X-Request-ID':identifier})
    with manufacturing_operation(session,identifier):
        return await BatchExecutionAccess(session,connector_for(request,principal)).batches(principal,work_order_id)


@router.get('/work-orders/{work_order_id}/batches/{batch_id}/steps')
async def batch_steps(work_order_id: Identifier, batch_id: Identifier, request: Request, response: Response,
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier=uuid4().hex; response.headers.update({'Cache-Control':'no-store','X-Request-ID':identifier})
    with manufacturing_operation(session,identifier):
        return await BatchExecutionAccess(session,connector_for(request,principal)).steps(principal,work_order_id,batch_id)


@router.get('/work-orders/{work_order_id}/execution-context')
async def batch_execution_context(work_order_id: Identifier, request: Request, response: Response,
        batch_id: Identifier | None = None, step_id: Identifier | None = None, expected_source_revision: Identifier | None = None,
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    if step_id is not None and batch_id is None:
        raise HTTPException(422,'选择步骤时必须指定所属批次。')
    identifier=uuid4().hex; response.headers.update({'Cache-Control':'no-store','X-Request-ID':identifier})
    with manufacturing_operation(session,identifier):
        value=await BatchExecutionAccess(session,connector_for(request,principal)).context(principal,work_order_id,
            batch_id=batch_id,step_id=step_id,expected_source_revision=expected_source_revision)
        return public_execution(value)


@router.get('/work-orders/{work_order_id}/operations')
async def work_order_operations(work_order_id: Identifier, request: Request, response: Response,
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier = uuid4().hex; response.headers.update({'Cache-Control': 'no-store', 'X-Request-ID': identifier})
    with manufacturing_operation(session, identifier):
        return await WorkOrderAccessService(session, connector_for(request, principal)).list_operations(
            principal=principal, work_order_id=work_order_id)


@router.get('/work-orders/{work_order_id}/operations/{operation_id}/job-cards')
async def operation_job_cards(work_order_id: Identifier, operation_id: Identifier, request: Request, response: Response,
        limit: int = Query(20, ge=1, le=20), cursor: str | None = Query(None, min_length=1, max_length=1024),
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier = uuid4().hex; response.headers.update({'Cache-Control': 'no-store', 'X-Request-ID': identifier})
    with manufacturing_operation(session, identifier):
        return await WorkOrderAccessService(session, connector_for(request, principal)).list_job_cards(
            principal=principal, work_order_id=work_order_id, operation_id=operation_id, cursor=cursor, limit=limit)


@router.get("/work-orders/{work_order_id}/context")
async def work_order_context(work_order_id: Identifier, request: Request, response: Response,
        operation_id: Identifier | None = None, job_card_id: Identifier | None = None,
        batch_id: Identifier | None = None, step_id: Identifier | None = None,
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    if job_card_id is not None and operation_id is None:
        raise HTTPException(422, '选择作业卡时必须指定所属工序。')
    if step_id is not None and batch_id is None or batch_id is not None and operation_id is not None:
        raise HTTPException(422,'批次/步骤父级缺失或混用了不同执行层级。')
    identifier = uuid4().hex; response.headers.update({"Cache-Control": "no-store", "X-Request-ID": identifier})
    with manufacturing_operation(session, identifier):
        context = await WorkOrderAccessService(session, connector_for(request, principal)).read_context(
            principal=principal, work_order_id=work_order_id, operation_id=operation_id, job_card_id=job_card_id,batch_id=batch_id,step_id=step_id)
        return public_context(context)


def conversation_store(session, principal, knowledge_base_id):
    invalidate_revoked_conversations(session, principal)
    try:
        scope = AuthorizationService(session).require_retrieval_scope(principal=principal, knowledge_base_id=knowledge_base_id)
    except AuthorizationDenied:
        invalidate_scope_bindings(session, principal, knowledge_base_id)
        raise
    return ManufacturingConversationStore(session, principal, scope)


def conversation_summary(store, row):
    title = store.safe_title(row)
    binding = store.binding(row.id)
    return dict(id=row.id, knowledge_base_id=row.knowledge_base_id, title=title,
        turn_count=row.turn_count, created_at=row.created_at, updated_at=row.updated_at,
        binding=dict(work_order_id=binding.work_order_id, source_system_id=binding.source_system_id,
            source_revision=binding.source_revision, invalidated=binding.invalidated_at is not None,
            **selection_kwargs(binding)))


@router.post("/conversations", status_code=201)
async def create_conversation(payload: ConversationInput, request: Request, response: Response,
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier = uuid4().hex; response.headers.update({"Cache-Control": "no-store", "X-Request-ID": identifier})
    with manufacturing_operation(session, identifier):
        store = conversation_store(session, principal, payload.knowledge_base_id)
        access = WorkOrderAccessService(session, connector_for(request, principal))
        context = await access.read_context(principal=principal, work_order_id=payload.work_order_id,
            expected_source_revision=payload.expected_source_revision, **selection_kwargs(payload))
        # Reauthorize after the source await before storing a business binding.
        AuthorizationService(session).require_retrieval_scope(principal=principal, knowledge_base_id=payload.knowledge_base_id)
        row = store.create_bound(context)
        AuditService(session).add(tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
            action="manufacturing.conversation_created", resource_type="conversation", resource_id=row.id,
            outcome="success", knowledge_base_id=payload.knowledge_base_id)
        session.commit()
        return conversation_summary(store, row)


@router.get("/conversations")
def list_conversations(response: Response, knowledge_base_id: UUID | None = None,
        limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier = uuid4().hex; response.headers.update({"Cache-Control": "no-store", "X-Request-ID": identifier})
    with manufacturing_operation(session, identifier):
        store = conversation_store(session, principal, knowledge_base_id)
        return dict(items=[conversation_summary(store, row) for row in store.list(limit, offset)])


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: UUID, response: Response, knowledge_base_id: UUID | None = None,
        limit: int = Query(100, ge=1, le=100), before: int | None = Query(None, ge=1),
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier = uuid4().hex; response.headers.update({"Cache-Control": "no-store", "X-Request-ID": identifier})
    with manufacturing_operation(session, identifier):
        store = conversation_store(session, principal, knowledge_base_id); row = store.get(conversation_id)
        turns = store.turns(conversation_id, limit=limit, before=before)
        runs = dict(session.execute(select(QARun.turn_index, QARun.id).where(QARun.tenant_id == principal.tenant_id,
            QARun.user_id == principal.user_id, QARun.conversation_id == conversation_id,
            QARun.turn_index.in_([turn.turn_index for turn in turns]))).all())
        return dict(**conversation_summary(store, row), turns=[dict(run_id=runs.get(turn.turn_index), turn_index=turn.turn_index, question=turn.question,
            result=AnswerResult.model_validate(turn.result_json).model_dump(mode="json"), created_at=turn.created_at)
            for turn in turns], next_before=turns[0].turn_index if turns and turns[0].turn_index > 1 else None)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, knowledge_base_id: UUID | None = None,
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier = uuid4().hex
    with manufacturing_operation(session, identifier):
        store = conversation_store(session, principal, knowledge_base_id)
        store.delete(conversation_id, commit=False)
        AuditService(session).add(tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
            action="manufacturing.conversation_deleted", resource_type="conversation", resource_id=conversation_id,
            outcome="success", knowledge_base_id=knowledge_base_id)
        session.commit(); return Response(status_code=204, headers={"Cache-Control": "no-store", "X-Request-ID": identifier})


def qa_failure(session, principal, payload, request_id, exc):
    if payload.conversation_id is not None and isinstance(exc, (MESError, BusinessAuthorizationDenied,
            AuthorizationDenied, ManufacturingScopeChanged)):
        invalidate_binding(session, principal, payload.conversation_id)
    # Usage has its own transaction. A storage outage during failure auditing
    # must not erase it or turn this into a successful QA response.
    try:
        AuditService(session).record(tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
            action="manufacturing.qa_queried", resource_type="knowledge_base", outcome="failed", request_id=request_id,
            details=dict(error_type=type(exc).__name__))
    except SQLAlchemyError as audit_error:
        session.rollback()
        log_internal_error(audit_error, request_id, stage="failure_audit")


@router.post("/qa", response_model=ManufacturingResponse)
async def manufacturing_qa(payload: ManufacturingExecutionQuestion, request: Request, response: Response,
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier = uuid4().hex; response.headers.update({"Cache-Control": "no-store", "X-Request-ID": identifier})
    lease = None
    with manufacturing_operation(session, identifier,
            failed=lambda exc: qa_failure(session, principal, payload, identifier, exc)):
        limiter = request.app.state.tenant_concurrency_limiter
        try:
            scope = AuthorizationService(session).require_retrieval_scope(principal=principal, knowledge_base_id=payload.knowledge_base_id)
            connector = connector_for(request, principal)
            WorkOrderAccessService(session, connector).scope(principal)
            rate = request.app.state.tenant_rate_limiter.consume(tenant_id=principal.tenant_id, resource="qa",
                limit=request.app.state.settings.qa_rate_limit_requests,
                window_seconds=request.app.state.settings.qa_rate_limit_window_seconds)
            if not rate.allowed: raise TenantRateLimitExceeded(rate)
            response.headers.update(rate_limit_headers(rate))
            concurrent = limiter.acquire(tenant_id=principal.tenant_id, resource="qa",
                limit=request.app.state.settings.qa_max_concurrent_requests_per_tenant,
                lease_seconds=request.app.state.settings.qa_concurrency_lease_seconds)
            if not concurrent.allowed: raise TenantConcurrencyExceeded(concurrent)
            lease = concurrent.lease
            if lease is None: raise ConcurrencyLimiterUnavailable("缺少并发租约。")
            response.headers.update(concurrency_headers(concurrent))
            fields = dict(run_id=None, snapshot_status="not_observed")
            with maintain_concurrency_lease(limiter, lease, lease_seconds=request.app.state.settings.qa_concurrency_lease_seconds) as check:
                with usage_context(scope=scope, session=session, settings=request.app.state.settings,
                    request_id=identifier, actor_user_id=principal.user_id, actor_external_subject=principal.external_subject,
                    conversation_id=payload.conversation_id) as usage, capture_snapshot(scope, payload.question,
                        enabled=getattr(request.app.state.settings, "qa_snapshots_enabled", True)) as observation:
                    def commit(result, selected_scope, snapshot):
                        check()
                        if observation is not None:
                            run = save_run(session, run_id=UUID(identifier), request_id=identifier, principal=principal,
                                scope=selected_scope, question=payload.question, result=result, capture=observation,
                                settings=request.app.state.settings, conversation_id=payload.conversation_id,
                                model_usage=usage.events, manufacturing=snapshot)
                            fields.update(run_id=run.id, snapshot_status=run.snapshot_status)
                        AuditService(session).add(tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
                            knowledge_base_id=scope.knowledge_base_id, action="manufacturing.qa_queried", resource_type="qa_run",
                            resource_id=fields["run_id"], outcome="success", request_id=identifier,
                            details=dict(answerable=result.answerable, citation_count=len(result.citations)))
                        session.commit()
                    service = ManufacturingService(session=session, principal=principal, connector=connector,
                        answer_service=request.app.state.answer_service,
                        resolver=getattr(getattr(request.app.state, "conversation_answer_service", None), "resolver", None))
                    result, elapsed = await service.answer(payload, scope=scope, observation=observation,
                        commit_success=commit, check_lease=check)
            return ManufacturingResponse(**result.model_dump(), **fields, request_id=identifier,
                conversation_id=payload.conversation_id, manufacturing_total_ms=elapsed)
        finally:
            if lease is not None:
                try: limiter.release(lease)
                except ConcurrencyLimiterUnavailable: pass


from .step_guidance import router as step_guidance_router
router.include_router(step_guidance_router)
