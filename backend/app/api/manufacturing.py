"""Authenticated, read-only work-order guidance over the shared QA services."""
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field, ValidationError
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
from ..knowledge.conversations import ConversationBusy, ConversationNotFound
from ..manufacturing.access import WorkOrderAccessService
from ..manufacturing.contracts import Contract, Identifier, ManufacturingQuestion
from ..manufacturing.conversations import ManufacturingConversationStore
from ..manufacturing.history_access import invalidate_binding, invalidate_revoked_conversations, invalidate_scope_bindings
from ..manufacturing.scope import ManufacturingScopeChanged
from ..manufacturing.service import ManufacturingService, public_context
from ..model_usage import usage_context
from ..quality.capture import capture_snapshot
from ..quality.service import save_run
from ..security.authorization import AuthorizationDenied, AuthorizationService
from ..security.business_scope import BusinessAuthorizationDenied
from ..security.dependencies import get_current_principal
from ..security.principal import Principal
from .qa import rate_limit_headers, concurrency_headers

router = APIRouter(prefix="/api/manufacturing", tags=["manufacturing"])


class ConversationInput(Contract):
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
        code, name, message = 409, "conversation_busy", str(exc)
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
    elif isinstance(exc, ValueError):
        code, name, message = 422, "manufacturing_input_invalid", "请求或追问条件无法验证，请重新查询。"
    else:
        code, name, message = 503, "manufacturing_unavailable", "制造问答服务暂不可用，请重新查询。"
    return HTTPException(code, dict(error_code=name, message=message, request_id=request_id), headers=headers)


@router.get("/work-orders")
async def work_orders(request: Request, response: Response, limit: int = Query(20, ge=1, le=20),
        cursor: str | None = Query(None, min_length=1, max_length=1024),
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier = uuid4().hex; response.headers.update({"Cache-Control": "no-store", "X-Request-ID": identifier})
    try:
        page = await WorkOrderAccessService(session, connector_for(request, principal)).list_work_orders(
            principal=principal, cursor=cursor, limit=limit)
        return dict(items=[row.model_dump(mode="json", exclude={"tenant_id", "schema_version"}) for row in page.items],
                    next_cursor=page.next_cursor)
    except Exception as exc:
        raise fail(exc, identifier) from None


@router.get("/work-orders/{work_order_id}/context")
async def work_order_context(work_order_id: Identifier, request: Request, response: Response,
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier = uuid4().hex; response.headers.update({"Cache-Control": "no-store", "X-Request-ID": identifier})
    try:
        context = await WorkOrderAccessService(session, connector_for(request, principal)).read_context(
            principal=principal, work_order_id=work_order_id)
        return public_context(context)
    except Exception as exc:
        raise fail(exc, identifier) from None


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
            source_revision=binding.source_revision, invalidated=binding.invalidated_at is not None))


@router.post("/conversations", status_code=201)
async def create_conversation(payload: ConversationInput, request: Request, response: Response,
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier = uuid4().hex; response.headers.update({"Cache-Control": "no-store", "X-Request-ID": identifier})
    try:
        store = conversation_store(session, principal, payload.knowledge_base_id)
        access = WorkOrderAccessService(session, connector_for(request, principal))
        context = await access.read_context(principal=principal, work_order_id=payload.work_order_id,
            expected_source_revision=payload.expected_source_revision)
        # Reauthorize after the source await before storing a business binding.
        AuthorizationService(session).require_retrieval_scope(principal=principal, knowledge_base_id=payload.knowledge_base_id)
        row = store.create_bound(context)
        AuditService(session).add(tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
            action="manufacturing.conversation_created", resource_type="conversation", resource_id=row.id,
            outcome="success", knowledge_base_id=payload.knowledge_base_id)
        session.commit()
        return conversation_summary(store, row)
    except Exception as exc:
        session.rollback(); raise fail(exc, identifier) from None


@router.get("/conversations")
def list_conversations(response: Response, knowledge_base_id: UUID | None = None,
        limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    response.headers["Cache-Control"] = "no-store"
    try:
        store = conversation_store(session, principal, knowledge_base_id)
        return dict(items=[conversation_summary(store, row) for row in store.list(limit, offset)])
    except Exception as exc:
        raise fail(exc, uuid4().hex) from None


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: UUID, response: Response, knowledge_base_id: UUID | None = None,
        limit: int = Query(100, ge=1, le=100), before: int | None = Query(None, ge=1),
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    response.headers["Cache-Control"] = "no-store"
    try:
        store = conversation_store(session, principal, knowledge_base_id); row = store.get(conversation_id)
        turns = store.turns(conversation_id, limit=limit, before=before)
        runs = dict(session.execute(select(QARun.turn_index, QARun.id).where(QARun.tenant_id == principal.tenant_id,
            QARun.user_id == principal.user_id, QARun.conversation_id == conversation_id,
            QARun.turn_index.in_([turn.turn_index for turn in turns]))).all())
        return dict(**conversation_summary(store, row), turns=[dict(run_id=runs.get(turn.turn_index), turn_index=turn.turn_index, question=turn.question,
            result=AnswerResult.model_validate(turn.result_json).model_dump(mode="json"), created_at=turn.created_at)
            for turn in turns], next_before=turns[0].turn_index if turns and turns[0].turn_index > 1 else None)
    except Exception as exc:
        raise fail(exc, uuid4().hex) from None


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, knowledge_base_id: UUID | None = None,
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    try:
        store = conversation_store(session, principal, knowledge_base_id)
        store.delete(conversation_id, commit=False)
        AuditService(session).add(tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
            action="manufacturing.conversation_deleted", resource_type="conversation", resource_id=conversation_id,
            outcome="success", knowledge_base_id=knowledge_base_id)
        session.commit(); return Response(status_code=204, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        session.rollback(); raise fail(exc, uuid4().hex) from None


@router.post("/qa", response_model=ManufacturingResponse)
async def manufacturing_qa(payload: ManufacturingQuestion, request: Request, response: Response,
        principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    identifier = uuid4().hex; response.headers.update({"Cache-Control": "no-store", "X-Request-ID": identifier})
    lease = None; limiter = request.app.state.tenant_concurrency_limiter
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
    except Exception as exc:
        session.rollback()
        if payload.conversation_id is not None and isinstance(exc, (MESError, BusinessAuthorizationDenied,
                AuthorizationDenied, ManufacturingScopeChanged)):
            invalidate_binding(session, principal, payload.conversation_id)
        # Usage is committed independently of the failed business transaction.
        try:
            AuditService(session).record(tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
                action="manufacturing.qa_queried", resource_type="knowledge_base", outcome="failed", request_id=identifier,
                details=dict(error_type=type(exc).__name__))
        except Exception:
            session.rollback()
        raise fail(exc, identifier) from None
    finally:
        if lease is not None:
            try: limiter.release(lease)
            except ConcurrencyLimiterUnavailable: pass
