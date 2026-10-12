"""Read-only scoped administrator access to the physical-call ledger."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, aliased

from ..db.dependencies import get_database_session
from ..db.model_usage_models import ModelUsageEvent as Event, ModelUsageScope as Scope
from ..security.authorization import AuthorizationDenied, AuthorizationService
from ..security.dependencies import get_current_principal
from ..security.principal import Principal

router = APIRouter(prefix="/api/knowledge-bases", tags=["model-usage"])


def visible_events(session, principal, knowledge_base_id, since, until, request_id=None):
    authorization = AuthorizationService(session)
    try:
        authorization.require_permission(principal=principal, knowledge_base_id=knowledge_base_id, required_permission="admin")
    except AuthorizationDenied as exc:
        raise HTTPException(403, "没有权限查看该知识库的模型用量。") from exc
    admins = {kb for kb in authorization.list_readable_knowledge_base_ids(principal=principal)
              if authorization.get_permission(principal=principal, knowledge_base_id=kb) == "admin"}
    related, forbidden = aliased(Scope), aliased(Scope)
    predicates = [Event.tenant_id == principal.tenant_id, Event.created_at >= since, Event.created_at < until,
                  exists(select(related.event_id).where(related.event_id == Event.id,
                         related.tenant_id == Event.tenant_id, related.knowledge_base_id == knowledge_base_id)),
                  ~exists(select(forbidden.event_id).where(forbidden.event_id == Event.id,
                          forbidden.tenant_id == Event.tenant_id, forbidden.knowledge_base_id.not_in(admins)))]
    if request_id:
        predicates.append(Event.request_id == request_id)
    return predicates


def window(since, until):
    until = until or datetime.now(timezone.utc)
    since = since or until - timedelta(days=7)
    if since.tzinfo is None or until.tzinfo is None or not timedelta(0) < until - since <= timedelta(days=90):
        raise HTTPException(422, "请提供带时区的起止时间，范围应在 0 至 90 天之间。")
    return since, until


@router.get("/{knowledge_base_id}/model-usage")
def list_usage(knowledge_base_id: UUID, since: datetime | None = None, until: datetime | None = None,
               request_id: str | None = Query(None, min_length=1, max_length=64),
               before: datetime | None = None, before_id: UUID | None = None,
               limit: int = Query(50, ge=1, le=100),
               principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    since, until = window(since, until)
    predicates = visible_events(session, principal, knowledge_base_id, since, until, request_id)
    if (before is None) != (before_id is None) or before is not None and before.tzinfo is None:
        raise HTTPException(422, "分页须同时提供带时区的 before 和 before_id。")
    if before is not None:
        predicates.append((Event.created_at < before) | ((Event.created_at == before) & (Event.id < before_id)))
    rows = session.scalars(select(Event).where(*predicates).order_by(Event.created_at.desc(), Event.id.desc()).limit(limit)).all()
    fields = ("id", "request_id", "actor_user_id", "stage", "provider", "model", "created_at", "status",
              "input_tokens", "output_tokens", "total_tokens", "estimated_cost", "currency", "details")
    items = [{key: str(getattr(row, key)) if key == "estimated_cost" and row.estimated_cost is not None else getattr(row, key)
              for key in fields} for row in rows]
    return dict(items=items, since=since, until=until,
                next_cursor=dict(before=rows[-1].created_at.replace(tzinfo=timezone.utc), before_id=rows[-1].id) if len(rows) == limit else None,
                attribution="Shared calls are counted once; per-KB views are not additive. Requires admin on all linked KBs.")


@router.get("/{knowledge_base_id}/model-usage/summary")
def usage_summary(knowledge_base_id: UUID, since: datetime | None = None, until: datetime | None = None,
                  request_id: str | None = Query(None, min_length=1, max_length=64),
                  principal: Principal = Depends(get_current_principal), session: Session = Depends(get_database_session)):
    since, until = window(since, until)
    predicates = visible_events(session, principal, knowledge_base_id, since, until, request_id)
    columns = (Event.stage, Event.provider, Event.model, Event.currency, Event.status)
    rows = session.execute(select(*columns, func.count().label("calls"), func.count(Event.total_tokens).label("known_calls"),
                                  func.sum(Event.total_tokens).label("reported_total_tokens"),
                                  func.count(Event.estimated_cost).label("priced_calls"), func.sum(Event.estimated_cost).label("estimated_cost"))
                           .where(*predicates).group_by(*columns)).mappings().all()
    groups = []
    for row in rows:
        result = dict(row)
        result["unknown_usage_calls"] = result["calls"] - result.pop("known_calls")
        result["unpriced_calls"] = result["calls"] - result.pop("priced_calls")
        result["estimated_cost"] = str(result["estimated_cost"]) if result["estimated_cost"] is not None else None
        groups.append(result)
    return dict(since=since, until=until, groups=groups,
                limitation="Partial known totals and configured-price estimates only; currencies must not be added together.")
