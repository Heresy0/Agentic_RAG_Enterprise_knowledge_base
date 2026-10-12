"""Usage accounting at physical-call boundaries; never collect prompt or credentials."""
import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from time import perf_counter
from uuid import uuid4

from prometheus_client import Counter, Histogram
from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from .db.model_usage_models import ModelUsageEvent, ModelUsageScope

logger = logging.getLogger(__name__)
_current = ContextVar("model_usage_context", default=None)
STAGES = {"answer_generation", "conversation_rewrite", "query_embedding", "document_embedding", "rerank"}
CALLS = Counter("enterprise_model_calls", "Physical model attempts", ["stage", "provider", "model", "status"])
TOKENS = Counter("enterprise_model_tokens", "Reported total tokens (not estimates)", ["stage", "provider", "model"])
UNKNOWN = Counter("enterprise_model_usage_unknown", "Calls without trustworthy total usage", ["stage", "provider", "model"])
COST = Counter("enterprise_model_estimated_cost", "Configured-price estimates, not supplier bills", ["provider", "model", "currency"])
LATENCY = Histogram("enterprise_model_call_seconds", "Physical model call latency", ["stage", "provider", "model"])
WRITE_FAILURES = Counter("enterprise_model_usage_write_failures", "Independent ledger write failures", ["operation"])


def parse_prices(raw):
    """Reject malformed configuration without logging its contents."""
    try:
        entries = json.loads(raw or "{}")
        if not isinstance(entries, dict):
            raise ValueError()
        prices = {}
        allowed = {"version", "currency", "input_per_million", "output_per_million", "cached_input_per_million", "total_per_million"}
        for key, value in entries.items():
            if not isinstance(value, dict) or set(value) - allowed or len(key) > 170 or ":" not in key:
                raise ValueError()
            if not re.fullmatch(r"[A-Z]{3}", value.get("currency", "")) or not re.fullmatch(r"[\w.-]{1,64}", value.get("version", "")):
                raise ValueError()
            price = dict(value)
            for name in allowed - {"version", "currency"}:
                if name in price:
                    rate = Decimal(str(price[name]))
                    if not rate.is_finite() or rate < 0 or rate > 1000000:
                        raise ValueError()
                    price[name] = str(rate)
            if "total_per_million" in price:
                if set(price) != {"version", "currency", "total_per_million"}:
                    raise ValueError()
            elif not {"input_per_million", "output_per_million"} <= set(price):
                raise ValueError()
            prices[key] = price
        return prices
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError("MODEL_USAGE_PRICES_JSON 格式无效；请核对版本、币种和非负单价。") from None


def _count(value):
    return value if type(value) is int and 0 <= value <= 2147483647 else None


def normalize_usage(raw):
    raw = raw if isinstance(raw, dict) else {}
    inp = _count(raw.get("input_tokens", raw.get("prompt_tokens")))
    out = _count(raw.get("output_tokens", raw.get("completion_tokens")))
    total = _count(raw.get("total_tokens"))
    origin = "provider" if total is not None else "unknown"
    if total is None and inp is not None and out is not None:
        total, origin = _count(inp + out), "provider_components"
        if total is None:
            origin = "unknown"
    input_details = raw.get("input_token_details", raw.get("prompt_tokens_details")) or {}
    output_details = raw.get("output_token_details", raw.get("completion_tokens_details")) or {}
    cached = _count(input_details.get("cache_read", input_details.get("cached_tokens"))) if isinstance(input_details, dict) else None
    reasoning = _count(output_details.get("reasoning", output_details.get("reasoning_tokens"))) if isinstance(output_details, dict) else None
    # Inconsistent counts are not silently treated as billable truth.
    invalid = (inp is not None and out is not None and total is not None and inp + out != total
               or cached is not None and inp is not None and cached > inp
               or reasoning is not None and out is not None and reasoning > out
               or total is not None and any(n is not None and n > total for n in (inp, out)))
    if invalid:
        inp = out = total = cached = reasoning = None
        origin = "unknown"
    return dict(input_tokens=inp, output_tokens=out, total_tokens=total,
                cached_input_tokens=cached, reasoning_tokens=reasoning, usage_source=origin)


def estimate_cost(usage, price):
    if not price or usage["total_tokens"] is None:
        return None
    if "total_per_million" in price:
        return Decimal(usage["total_tokens"]) * Decimal(price["total_per_million"]) / 1000000
    inp, out = usage["input_tokens"], usage["output_tokens"]
    if inp is None or out is None:
        return None
    cached = usage["cached_input_tokens"] or 0
    # Missing cache detail is priced at the standard input rate; this is an estimate.
    if cached and "cached_input_per_million" not in price:
        return None
    return (Decimal(inp - cached) * Decimal(price["input_per_million"])
            + Decimal(cached) * Decimal(price.get("cached_input_per_million", "0"))
            + Decimal(out) * Decimal(price["output_per_million"])) / 1000000


class UsageSink:
    def __init__(self, factory):
        self.factory = factory

    def begin(self, row, knowledge_base_ids):
        with self.factory() as session:
            session.add(ModelUsageEvent(**row))
            session.flush()
            session.add_all(ModelUsageScope(event_id=row["id"], tenant_id=row["tenant_id"], knowledge_base_id=kb)
                            for kb in knowledge_base_ids)
            session.commit()

    def finish(self, event_id, tenant_id, values):
        with self.factory() as session:
            result = session.execute(update(ModelUsageEvent).where(
                ModelUsageEvent.id == event_id, ModelUsageEvent.tenant_id == tenant_id,
                ModelUsageEvent.status == "pending",
            ).values(**values))
            session.commit()
            return result.rowcount == 1


@dataclass
class UsageContext:
    scope: object
    request_id: str
    actor_user_id: object = None
    sink: UsageSink | None = None
    prices: dict = field(default_factory=dict)
    links: dict = field(default_factory=dict)
    events: list = field(default_factory=list)


@contextmanager
def usage_context(*, scope, session=None, settings=None, request_id=None, actor_user_id=None, **links):
    from .evaluation.trace import start_model_usage
    start_model_usage()
    parent = _current.get()
    if parent is not None:
        if scope.tenant_id != parent.scope.tenant_id or not scope.knowledge_base_ids <= parent.scope.knowledge_base_ids:
            raise ValueError("模型审计上下文不能扩大授权范围。")
        yield parent
        return
    sink = None
    if session is not None and hasattr(session, "get_bind"):
        # A fresh Engine session, NOT the request session/connection: business rollback must not erase usage.
        bind = session.get_bind()
        from sqlalchemy.engine import Engine
        # SQLite in-memory tests can share one connection; independent commits there
        # would accidentally commit the business transaction. Production is PostgreSQL.
        if isinstance(bind, Engine) and bind.dialect.name == "postgresql":
            sink = UsageSink(sessionmaker(bind=bind, expire_on_commit=False))
    context = UsageContext(scope, request_id or uuid4().hex, actor_user_id, sink,
                           getattr(settings, "model_usage_prices", {}),
                           {k: str(v) for k, v in links.items() if k in {"conversation_id", "job_id", "document_id"} and v is not None})
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def audited_scope(function):
    """Bind direct service/CLI/evaluation entry points as well as HTTP calls."""
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        principal = kwargs.get("principal")
        with usage_context(scope=kwargs["scope"], session=kwargs.get("session", getattr(self, "_session", None)),
                           settings=getattr(self, "_settings", None),
                           actor_user_id=getattr(principal, "user_id", kwargs.get("created_by_user_id")),
                           conversation_id=kwargs.get("conversation_id"), document_id=kwargs.get("document_id")):
            return function(self, *args, **kwargs)
    return wrapped


class ModelCall:
    def __init__(self, stage, model, provider="dashscope"):
        if stage not in STAGES:
            raise ValueError("Unknown model usage stage")
        self.stage, self.model, self.provider = stage, model, provider
        self.id, self.usage, self.provider_request_id = uuid4(), normalize_usage(None), None
        self.context = _current.get()
        self.persisted = False
        self.finished = False

    def _write(self, operation, *args):
        try:
            written = getattr(self.context.sink, operation)(*args)
            if written is False:
                raise RuntimeError("No pending ledger event was finalized")
            return True
        except Exception as exc:
            WRITE_FAILURES.labels(operation).inc()
            logger.error("model_usage.write_failed operation=%s error_type=%s event_id=%s", operation, type(exc).__name__, self.id)
            return False

    def __enter__(self):
        self.started = perf_counter()
        context = self.context
        if context is not None and context.sink is not None:
            self.persisted = self._write("begin", dict(
                id=self.id, tenant_id=context.scope.tenant_id, request_id=context.request_id,
                actor_user_id=context.actor_user_id, stage=self.stage, provider=self.provider, model=self.model,
                created_at=datetime.now(timezone.utc), status="pending",
                details=dict(context.links, usage_source="unknown")), context.scope.knowledge_base_ids)
        return self

    def observe(self, raw, provider_request_id=None):
        self.usage = normalize_usage(raw)
        # Supplier ID only, never exception bodies, arbitrary response metadata or LangChain run IDs.
        if isinstance(provider_request_id, str) and re.fullmatch(r"[\w.-]{1,128}", provider_request_id):
            self.provider_request_id = provider_request_id

    def __exit__(self, exc_type, exc, tb):
        if self.finished:
            return False
        self.finished = True
        duration = perf_counter() - self.started
        status = "error" if exc_type else "success"
        price = self.context.prices.get(f"{self.provider}:{self.model}") if self.context else None
        cost = estimate_cost(self.usage, price)
        details = dict(self.context.links if self.context else {},
                       usage_source=self.usage["usage_source"], cached_input_tokens=self.usage["cached_input_tokens"],
                       reasoning_tokens=self.usage["reasoning_tokens"], duration_ms=round(duration * 1000, 3),
                       error_type=exc_type.__name__ if exc_type else None,
                       provider_request_id=self.provider_request_id, price_snapshot=price)
        values = dict(status=status, **{key: self.usage[key] for key in ("input_tokens", "output_tokens", "total_tokens")},
                      estimated_cost=cost, currency=price["currency"] if price else None, details=details)
        stored = self._write("finish", self.id, self.context.scope.tenant_id, values) if self.persisted else False
        row = dict(event_id=str(self.id), request_id=self.context.request_id if self.context else None,
                   stage=self.stage, provider=self.provider, model=self.model,
                   **{**values, "estimated_cost": str(cost) if cost is not None else None}, persisted=stored)
        if self.context is not None:
            self.context.events.append(row)
        from .evaluation.trace import record_model_usage
        record_model_usage(row)
        labels = (self.stage, self.provider, self.model)
        CALLS.labels(*labels, status).inc()
        LATENCY.labels(*labels).observe(duration)
        if self.usage["total_tokens"] is None:
            UNKNOWN.labels(*labels).inc()
        else:
            TOKENS.labels(*labels).inc(self.usage["total_tokens"])
        if cost is not None:
            COST.labels(self.provider, self.model, price["currency"]).inc(float(cost))
        return False


def invoke_structured(model, messages, *, stage, model_name):
    with ModelCall(stage, model_name) as call:
        result = model.invoke(messages)
        if isinstance(result, dict) and "raw" in result:
            raw = result["raw"]
            usage = getattr(raw, "usage_metadata", None)
            metadata = getattr(raw, "response_metadata", {}) or {}
            call.observe(usage or metadata.get("token_usage"), metadata.get("request_id"))
            if result.get("parsing_error") is not None:
                raise result["parsing_error"]
            return result["parsed"]
        # Injected models may not expose raw usage; explicitly unknown, never count as zero.
        return result


def summarize_usage(events, *, observed=True):
    events = list({row["event_id"]: row for row in events}.values())
    known = [row for row in events if row.get("total_tokens") is not None]
    costs = {}
    for row in events:
        if row.get("estimated_cost") is not None:
            currency = row["currency"]
            costs[currency] = costs.get(currency, Decimal(0)) + Decimal(str(row["estimated_cost"]))
    return dict(observed=observed, calls=len(events) if observed else None,
                reported_total_tokens=sum(row["total_tokens"] for row in known) if observed else None,
                unknown_usage_calls=len(events) - len(known) if observed else None,
                unknown_usage_rate=(len(events) - len(known)) / len(events) if events else None,
                unpriced_calls=sum(row.get("estimated_cost") is None for row in events) if observed else None,
                unpersisted_calls=sum(not row.get("persisted", False) for row in events) if observed else None,
                estimated_cost_by_currency={key: str(value) for key, value in costs.items()},
                errors=sum(row.get("status") == "error" for row in events) if observed else None)
