"""A request-local deadline for optional agent work; never mutate shared clients."""
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

_deadline = ContextVar("evidence_tool_deadline", default=None)


def remaining_timeout(default):
    deadline = _deadline.get()
    if deadline is None:
        return default
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("证据收集预算已耗尽")
    return min(float(default), remaining)


def active_deadline():
    return _deadline.get() is not None


@contextmanager
def evidence_deadline(deadline):
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)
