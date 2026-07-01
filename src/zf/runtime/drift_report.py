"""Drift report projection — planned vs actual reconciliation (doc 65 P0).

`drift-report.v1` explains *why a task should not have run / cannot be done /
is stuck* by comparing the planned task-map dimension against actual runtime,
plus surfacing kernel-emitted violation evidence.

Red line (doc 65 §20.3): this projection **consumes** kernel verdicts, it does
not re-judge. Scope drift only surfaces existing ``scope.violation`` events; it
never re-runs a gate. Reconciliation drift (dependency / assignment) compares
plan vs actual — that is the projection's own job, not a second judgment — so
it may assign error/warning. Absence-of-evidence is only ever ``warning``/``info``
(the kernel already let the task reach its state). 守 I2/I7.

Pure function over an execution-graph + events.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj

EventSlice = Sequence[tuple[int, ZfEvent]]

_DONE_STATES = {"done", "cancelled"}
# A node is "in flight or past" if its writer has started — used to decide a
# dependency drift is real (work began before its blocker finished).
_STARTED_STATES = {"in_progress", "review", "test", "judge", "dispatched", "done"}


def build_drift_report(
    *,
    graph: dict[str, Any],
    events: EventSlice = (),
) -> dict[str, Any]:
    """Classify drift from an execution-graph + kernel events.

    P0 coverage: dependency / assignment / evidence (reconciliation) + scope
    (consume kernel ``scope.violation``). Runtime / artifact drift are P2.
    """

    nodes = graph.get("nodes", [])
    node_status = {n["task_id"]: _status(n) for n in nodes}
    scope_violations = _scope_violations(events)

    items: list[dict[str, Any]] = []
    for node in nodes:
        items.extend(_dependency_drift(node, node_status))
        items.extend(_assignment_drift(node))
        items.extend(_evidence_drift(node))
        items.extend(_scope_drift(node, scope_violations))

    summary = {"error": 0, "warning": 0, "info": 0}
    for item in items:
        sev = str(item.get("severity") or "info")
        if sev in summary:
            summary[sev] += 1

    if summary["error"]:
        status = "error"
    elif summary["warning"]:
        status = "warning"
    elif summary["info"]:
        status = "info"
    else:
        status = "ok"

    return redact_obj({
        "schema_version": "drift-report.v1",
        "status": status,
        "summary": summary,
        "items": items,
    })


def _dependency_drift(
    node: dict[str, Any],
    node_status: dict[str, str],
) -> list[dict[str, Any]]:
    """Writer started before a blocker finished — reconciliation, may be error."""
    task_id = node["task_id"]
    if _status(node) not in _STARTED_STATES:
        return []
    out: list[dict[str, Any]] = []
    for blocker in node.get("planned", {}).get("blocked_by", []):
        if node_status.get(blocker, "") not in _DONE_STATES:
            out.append({
                "kind": "dependency_drift",
                "severity": "error",
                "task_id": task_id,
                "message": f"started while blocker {blocker} not done",
                "planned": {"blocked_by": blocker},
                "actual": {"status": _status(node), "blocker_status": node_status.get(blocker, "missing")},
                "evidence_event_id": "",
                "recommended_action": "dispatch.blocked or re-map via orchestrator",
            })
    return out


def _assignment_drift(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Actual assignee role-prefix differs from planned owner_role."""
    planned = node.get("planned", {})
    owner_role = str(planned.get("owner_role") or "").strip()
    assigned = str(node.get("actual", {}).get("assigned_to") or "").strip()
    if not owner_role or not assigned:
        return []
    if _status(node) not in _STARTED_STATES:
        return []
    if _role_prefix(assigned) == _role_prefix(owner_role):
        return []
    affinity = node.get("actual", {}).get("affinity")
    if isinstance(affinity, dict):
        history = affinity.get("instances_history")
        if isinstance(history, list):
            history_roles = {
                _role_prefix(str(item))
                for item in history
                if str(item).strip()
            }
            if _role_prefix(owner_role) in history_roles:
                return []
    return [{
        "kind": "assignment_drift",
        "severity": "warning",
        "task_id": node["task_id"],
        "message": f"assigned to {assigned} but planned owner_role {owner_role}",
        "planned": {"owner_role": owner_role},
        "actual": {"assigned_to": assigned},
        "evidence_event_id": "",
        "recommended_action": "rebrief correct owner or update task-map",
    }]


def _evidence_drift(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Task done but no evidence events — warning only (kernel already passed it)."""
    if _status(node) != "done":
        return []
    if node.get("actual", {}).get("evidence_events"):
        return []
    return [{
        "kind": "evidence_drift",
        "severity": "warning",
        "task_id": node["task_id"],
        "message": "done with no recorded evidence events",
        "planned": {"verification": node.get("planned", {}).get("verification", "")},
        "actual": {"evidence_events": []},
        "evidence_event_id": "",
        "recommended_action": "verify gate evidence exists; route to review/test if missing",
    }]


def _scope_drift(
    node: dict[str, Any],
    scope_violations: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Surface kernel-emitted scope.violation events — consume, never re-judge."""
    task_id = node["task_id"]
    out: list[dict[str, Any]] = []
    for event_id in scope_violations.get(task_id, []):
        out.append({
            "kind": "scope_drift",
            "severity": "warning",
            "task_id": task_id,
            "message": "kernel emitted scope.violation for this task",
            "planned": {"scope": node.get("planned", {}).get("scope", [])},
            "actual": {},
            "evidence_event_id": event_id,
            "recommended_action": "kernel already flagged; route rework / re-map / override",
        })
    return out


def _scope_violations(events: EventSlice) -> dict[str, list[str]]:
    by_task: dict[str, list[str]] = {}
    for _seq, event in events:
        if event.type != "scope.violation":
            continue
        task_id = str(event.task_id or "").strip()
        if task_id and event.id:
            by_task.setdefault(task_id, []).append(event.id)
    return by_task


def _status(node: dict[str, Any]) -> str:
    return str(node.get("actual", {}).get("status") or "")


def _role_prefix(value: str) -> str:
    token = str(value or "").strip().lower()
    for sep in ("-", "_", ".", ":"):
        token = token.split(sep, 1)[0]
    return token
