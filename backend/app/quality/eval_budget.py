"""Request-local physical-call limits for an explicitly authorized evaluation job."""
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

_current = ContextVar("quality_evaluation_budget", default=None)


class EvaluationStopped(RuntimeError):
    pass


class EvaluationBudget:
    def __init__(self, limits, guard=lambda: None):
        self.limits, self.guard = limits, guard
        self.started, self.calls, self.charged_tokens, self.unknown_calls = monotonic(), 0, 0, 0
        self.limited_by = None

    def check(self):
        if self.limited_by is None and monotonic() - self.started > self.limits["max_seconds"]:
            self.limited_by = "time"
        if self.limited_by: raise EvaluationStopped(self.limited_by)

    def before_call(self):
        self.check()
        try: self.guard()
        except Exception:
            self.limited_by = "authorization_or_lease"
            raise EvaluationStopped(self.limited_by) from None
        if self.calls >= self.limits["max_model_calls"]: self.limited_by = "calls"
        elif self.charged_tokens + self.limits["reservation_tokens"] > self.limits["max_tokens"]: self.limited_by = "tokens"
        self.check()
        self.calls += 1

    def after_call(self, tokens):
        if tokens is None:
            self.unknown_calls += 1
        self.charged_tokens += tokens if tokens is not None else self.limits["reservation_tokens"]
        if self.charged_tokens > self.limits["max_tokens"]: self.limited_by = "tokens"

    def snapshot(self):
        return dict(calls=self.calls, charged_tokens=self.charged_tokens, unknown_calls=self.unknown_calls,
                    elapsed_seconds=round(monotonic() - self.started, 3), limited_by=self.limited_by)


@contextmanager
def evaluation_budget(budget):
    token = _current.set(budget)
    try: yield budget
    finally: _current.reset(token)


def current_budget():
    return _current.get()
