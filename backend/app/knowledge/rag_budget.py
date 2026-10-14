"""One request-local budget for optional planning, decisions, tools and repair.

The original retrieval/generation remain separately accounted. Unknown provider
usage consumes the conservative reservation; this is not a billing estimate.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import re
import unicodedata
from time import monotonic

from ..model_usage import current_usage_context


class RagBudgetExceeded(RuntimeError):
    pass


_current = ContextVar("optional_rag_budget", default=None)


def query_key(query):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", query)).casefold()


@dataclass
class RagBudget:
    max_calls: int = 4
    max_tokens: int = 32000
    max_seconds: float = 35
    max_searches: int = 3
    max_reads: int = 1
    calls: int = 0
    charged_tokens: int = 0
    seconds: float = 0
    searches: int = 1  # Includes the unchanged first retrieval.
    reads: int = 0
    feedback_attempts: int = 0
    regenerations: int = 0
    seen_queries: set[str] = field(default_factory=set)

    @classmethod
    def from_settings(cls, settings):
        return cls(max_calls=getattr(settings, "rag_max_optional_calls", 4),
                   max_tokens=getattr(settings, "rag_max_optional_tokens", 32000),
                   max_seconds=getattr(settings, "rag_max_optional_seconds", 35),
                   max_searches=getattr(settings, "rag_max_searches", 3),
                   max_reads=getattr(settings, "agent_max_reads", 1))

    @property
    def remaining_seconds(self):
        return max(0, self.max_seconds - self.seconds)

    @property
    def remaining_tokens(self):
        return max(0, self.max_tokens - self.charged_tokens)

    def check_model(self, *, prompt_bytes, output_tokens):
        reservation = prompt_bytes + output_tokens
        if (self.calls >= self.max_calls or reservation > self.remaining_tokens
                or self.remaining_seconds <= 0):
            raise RagBudgetExceeded("可选 RAG 模型预算已耗尽")

    def begin_feedback(self):
        if self.feedback_attempts >= 1:
            raise RagBudgetExceeded("本次定向补查已使用")
        if self.searches >= self.max_searches or self.remaining_seconds <= 0:
            raise RagBudgetExceeded("共享补查预算已耗尽")
        self.feedback_attempts += 1

    def note_query(self, query):
        self.seen_queries.add(query_key(query))

    @contextmanager
    def model_attempt(self, *, prompt_bytes, output_tokens, purpose=None):
        self.check_model(prompt_bytes=prompt_bytes, output_tokens=output_tokens)
        if purpose == "regeneration":
            if self.regenerations >= 1 or self.feedback_attempts != 1:
                raise RagBudgetExceeded("本次再生成已使用或尚未补查")
            self.regenerations += 1
        reservation = prompt_bytes + output_tokens
        self.calls += 1
        usage = current_usage_context()
        cursor = len(usage.events) if usage else 0
        started = monotonic()
        try:
            yield self.remaining_seconds
        finally:
            events = usage.events[cursor:] if usage else []
            # Unknown attempts, including failures, must not become free retries.
            known = sum(row["total_tokens"] for row in events if row.get("total_tokens") is not None)
            self.charged_tokens += (known if events and all(row.get("total_tokens") is not None
                                                          for row in events) else max(known, reservation))
            self.seconds += max(0, monotonic() - started)

    @contextmanager
    def tool_attempt(self, action, *, query=None):
        if self.remaining_seconds <= 0:
            raise RagBudgetExceeded("可选 RAG 时间预算已耗尽")
        if action == "search":
            if self.searches >= self.max_searches:
                raise RagBudgetExceeded("共享检索预算已耗尽")
            if query is not None and query_key(query) in self.seen_queries:
                raise RagBudgetExceeded("不能重复已执行的查询")
            self.searches += 1
            if query is not None:
                self.note_query(query)
        elif action == "read":
            if self.reads >= self.max_reads:
                raise RagBudgetExceeded("共享读取预算已耗尽")
            self.reads += 1
        else:
            raise ValueError("未知证据工具")
        started = monotonic()
        try:
            yield self.remaining_seconds
        finally:
            self.seconds += max(0, monotonic() - started)

    def snapshot(self):
        return dict(calls=self.calls, charged_tokens=self.charged_tokens,
                    optional_seconds=round(self.seconds, 3), searches=self.searches, reads=self.reads,
                    feedback_attempts=self.feedback_attempts, regenerations=self.regenerations)


def current_rag_budget():
    return _current.get()


@contextmanager
def rag_budget_scope(budget):
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)
