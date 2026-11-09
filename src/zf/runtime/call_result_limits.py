"""Provider-native call limits derived from immutable operation facts."""

from __future__ import annotations

from typing import Any, Mapping


def operation_parallel_ceiling(operation: Mapping[str, Any] | None) -> int:
    operation = operation or {}
    try:
        value = int(operation.get("provider_session_max_parallel_agents"))
    except (TypeError, ValueError):
        value = 6
    return 0 if value == 0 else max(1, min(6, value))


def operation_budget_ceiling(
    operation: Mapping[str, Any] | None,
) -> float | None:
    operation = operation or {}
    explicit = operation.get("risk_review_budget_usd")
    if explicit not in (None, ""):
        return _nonnegative_float(explicit) or None
    snapshot = operation.get("budget_snapshot")
    if not isinstance(snapshot, Mapping):
        return None
    for key in ("remaining_usd", "hard_limit_usd", "budget_usd"):
        if snapshot.get(key) is not None:
            return _nonnegative_float(snapshot[key])
    return None


def _nonnegative_float(value: Any) -> float | None:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


__all__ = ["operation_budget_ceiling", "operation_parallel_ceiling"]
