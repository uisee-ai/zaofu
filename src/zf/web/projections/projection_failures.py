"""Collect failures while preparing an atomic durable projection generation."""
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

_FAILURES: ContextVar[list[str] | None] = ContextVar("dashboard_projection_failures", default=None)


def record_projection_failure(name: str) -> None:
    failures = _FAILURES.get()
    if failures is not None:
        failures.append(name)


@contextmanager
def collect_projection_failures() -> Iterator[list[str]]:
    failures: list[str] = []
    token = _FAILURES.set(failures)
    try:
        yield failures
    finally:
        _FAILURES.reset(token)
