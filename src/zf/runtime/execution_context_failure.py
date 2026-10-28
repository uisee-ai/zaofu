"""Typed execution blockers that do not establish a product contract gap."""
from collections.abc import Mapping
from typing import Any


EXECUTION_CONTEXT_FAILURES = frozenset({
    "task_context_stale", "workspace_binding_mismatch", "runner_failure",
    "input_delivery_failed", "harness_failure", "runtime_result_admission",
})


def execution_context_failure(payload: Mapping[str, Any]) -> str:
    for key in ("failure_class", "blocker_kind"):
        value = str(payload.get(key) or "").strip().lower()
        if value in EXECUTION_CONTEXT_FAILURES:
            return value
    return ""
