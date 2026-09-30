"""Spies and assertions for product-project external integrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ExternalSinkSpy:
    """Record calls without performing network, git or filesystem side effects."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    failures_before_success: int = 0

    def invoke(self, operation: str, payload: Any = None) -> dict[str, Any]:
        attempt = len(self.calls) + 1
        self.calls.append({"operation": operation, "payload": payload, "attempt": attempt})
        if attempt <= self.failures_before_success:
            raise RuntimeError(f"synthetic integration failure on attempt {attempt}")
        return {"ok": True, "attempt": attempt}

    def count(self, operation: str) -> int:
        return sum(call["operation"] == operation for call in self.calls)


def run_with_retry(action: Callable[[], Any], *, max_attempts: int) -> Any:
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    last_error: Exception | None = None
    for _ in range(max_attempts):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - harness records provider failures
            last_error = exc
    raise RuntimeError(f"integration retry limit exceeded: {max_attempts}") from last_error

