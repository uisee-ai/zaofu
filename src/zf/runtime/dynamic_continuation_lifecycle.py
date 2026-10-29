"""Parent/child evidence boundaries for registered read-only continuations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

from zf.runtime.workflow_operation import TERMINAL_OPERATION_STATUSES, reduce_workflow_operations


SUCCESS_VERDICTS = frozenset({"passed", "approved", "success", "admit", "accepted", "no_gap"})


def parent_fence(operations: Mapping[str, dict], operation_id: str) -> tuple[str, str]:
    row = operations.get(operation_id)
    if row and row.get("status") == "settled" and row.get("admitted_call_result_ref"):
        return "", ""  # A parent's later closure cannot invalidate admitted history.
    seen: set[str] = set()
    while row:
        key = str(row.get("operation_id") or "")
        if key in seen:
            return "operation_parent_cycle", key
        seen.add(key)
        parent = operations.get(str(row.get("parent_operation_id") or ""))
        if row.get("operation_type") == "dynamic_read_only_workflow":
            if parent is None:
                return "dynamic_parent_missing", key
            if parent.get("status") in TERMINAL_OPERATION_STATUSES:
                return "dynamic_parent_terminal", str(parent.get("last_event_id") or "")
        row = parent
    return "", ""


def result_lifecycle_issues(events: list[ZfEvent], operation_id: str, *, successful: bool) -> list[dict[str, Any]]:
    operations = reduce_workflow_operations(events)
    reason, _ = parent_fence(operations, operation_id)
    issues = [{"field": "parent_operation_id", "code": reason}] if reason else []
    if not successful:
        return issues
    for child in operations.values():
        if child.get("parent_operation_id") != operation_id or child.get("operation_type") != "dynamic_read_only_workflow":
            continue
        if (
            child.get("status") != "settled"
            or not child.get("admitted_call_result_ref")
            or child.get("semantic_verdict") not in SUCCESS_VERDICTS
        ):
            issues.append({
                "field": "child_operations",
                "code": "required_dynamic_child_not_admitted",
                "message": str(child.get("operation_id") or ""),
            })
    return issues


def capacity_error(state_dir: Path, operations: Mapping[str, dict], action: Mapping, operation_id: str) -> str:
    """Reserve sibling work against one parent ceiling under its caller lock.

    Physical worker slots remain owned by the existing global scheduler. An
    unknown/interrupted child retains its reservation until evidence resolves
    it; status alone cannot claim that a provider has stopped.
    """
    limit = (action.get("budgets") or {}).get("max_children")
    if type(limit) is not int or limit < 1:
        return "bounded_max_children_required"
    used = 0
    for child in operations.values():
        if child.get("parent_operation_id") != action.get("parent_operation_id") or child.get("operation_type") != "dynamic_read_only_workflow":
            continue
        try:
            request = hydrate_sidecar_ref(state_dir, child["request_ref"]).payload["request"]
            previous_limit = request["budget_snapshot"]["proposal"]["max_children"]
            limit = min(limit, int(previous_limit))
            count = int(request.get("reserved_child_count") or previous_limit)
        except (KeyError, TypeError, ValueError):
            return "sibling_reservation_unknown"
        if child.get("operation_id") == operation_id:
            continue
        released = (
            child.get("status") == "settled" and child.get("admitted_call_result_ref")
        ) or (
            child.get("status") == "superseded" and not child.get("dispatch_id")
        )
        if not released:
            used += count
    count = int(action.get("reserved_child_count") or 1)
    return "parent_child_capacity_exceeded" if used + count > limit else ""
