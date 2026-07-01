"""Read-only Kanban board projection helpers.

Task status remains kernel truth. These helpers only project runtime state into
operator-facing board columns shared by Web, Feishu, and automation summaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from zf.core.task.schema import Task


KANBAN_COLUMN_LABELS: dict[str, str] = {
    "ready": "Todo",
    "in_progress": "In Progress",
    "testing": "Verify",
    "blocked": "Blocked",
    "done": "Done",
}
KANBAN_COLUMN_IDS: tuple[str, ...] = tuple(KANBAN_COLUMN_LABELS)
KANBAN_COLUMN_OPTIONS: tuple[str, ...] = tuple(KANBAN_COLUMN_LABELS.values())

_TODO_STATUSES = {"backlog", "ready", "todo", "planned", "pending"}
_VERIFY_STATUSES = {"review", "testing", "verify", "verifying", "judge"}
_DONE_STATUSES = {"done", "cancelled", "superseded", "archived"}
_VERIFY_ROLE_PREFIXES = {"review", "test", "verify", "verifier", "judge", "qa"}
_VERIFY_PHASES = {
    "build_done",
    "static_gate_passed",
    "static_gate_skipped",
    "review_requested",
    "review_approved",
    "verify_passed",
    "test_running",
    "test_passed",
    "judge_running",
    "judge_passed",
}
_FANOUT_QUEUE_REASON_PREFIX = "fanout_queue:"


@dataclass(frozen=True)
class KanbanColumnProjection:
    column: str
    label: str
    reason: str
    badges: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowProjection:
    """Read-only workflow projection for operator-facing task cards."""

    workflow_phase: str
    impl_exit_gate_state: str
    verify_state: str
    judge_state: str
    verify_lanes: tuple[dict[str, str], ...] = ()
    terminal_required_event: str = ""
    rework_target: str = ""
    rework_reason: str = ""
    badges: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_phase": self.workflow_phase,
            "impl_exit_gate_state": self.impl_exit_gate_state,
            "verify_state": self.verify_state,
            "judge_state": self.judge_state,
            "verify_lanes": [dict(item) for item in self.verify_lanes],
            "terminal_required_event": self.terminal_required_event,
            "rework_target": self.rework_target,
            "rework_reason": self.rework_reason,
            "badges": [dict(item) for item in self.badges],
        }


_IMPL_GATE_EVENTS = {
    "static_gate.passed": "passed",
    "static_gate.failed": "failed",
    "static_gate.skipped": "skipped",
}
_VERIFY_LANE_EVENTS: dict[str, tuple[str, str]] = {
    "review.approved": ("review", "passed"),
    "review.rejected": ("review", "failed"),
    "review.suspended": ("review", "blocked"),
    "test.passed": ("test", "passed"),
    "test.failed": ("test", "failed"),
    "test.suspended": ("test", "blocked"),
    "verify.passed": ("verify", "passed"),
    "verify.failed": ("verify", "failed"),
}
_JUDGE_EVENTS = {
    "judge.passed": "passed",
    "judge.failed": "failed",
}
_REWORK_EVENTS = {
    "static_gate.failed",
    "review.rejected",
    "test.failed",
    "verify.failed",
    "judge.failed",
}
_WORKFLOW_EVENTS = (
    set(_IMPL_GATE_EVENTS)
    | set(_VERIFY_LANE_EVENTS)
    | set(_JUDGE_EVENTS)
    | {"dev.build.done"}
)


def kanban_column_label(column: str) -> str:
    return KANBAN_COLUMN_LABELS.get(column, "Todo")


def kanban_column_projection(
    task: Task,
    *,
    phase: str | None = None,
    ready: bool = False,
    extra_badges: Iterable[str] = (),
) -> KanbanColumnProjection:
    """Project a task into the simplified board column model.

    The returned ``column`` is the UI/API id. ``label`` is suitable for
    human-facing integrations such as Feishu bitable select options.
    """
    status = str(task.status or "").strip().lower().replace("-", "_")
    phase_value = str(phase or "").strip().lower().replace("-", "_")
    badges = [str(item) for item in extra_badges if str(item)]

    if status in _DONE_STATUSES:
        return _projection("done", f"status:{status}", badges)
    if status == "blocked":
        if _is_fanout_queue_wait(task):
            badges.append("queued")
            return _projection("ready", "fanout_queue", badges)
        return _projection("blocked", "status:blocked", badges)
    if status in _TODO_STATUSES:
        if status == "backlog" and not ready:
            badges.append("not ready")
        return _projection("ready", f"status:{status}", badges)
    if status in _VERIFY_STATUSES:
        return _projection("testing", f"status:{status}", badges)

    if status == "in_progress":
        role = _role_prefix(task.assigned_to or "")
        if role in _VERIFY_ROLE_PREFIXES or phase_value in _VERIFY_PHASES:
            return _projection("testing", _workflow_reason(role, phase_value), badges)
        return _projection("in_progress", _workflow_reason(role, phase_value), badges)

    return _projection("ready", f"status:{status or 'unknown'}", badges)


def _is_fanout_queue_wait(task: Task) -> bool:
    reason = str(getattr(task, "blocked_reason", "") or "")
    return reason.startswith(_FANOUT_QUEUE_REASON_PREFIX)


def workflow_projection(
    task: Task,
    events: Iterable[object] = (),
    *,
    phase: str | None = None,
    judge_configured: bool = False,
    terminal_success_event: str = "",
) -> WorkflowProjection:
    """Project legacy workflow events into the product workflow model.

    This is intentionally read-only. It does not rename durable event types or
    mutate task state; it only lets UI/API consumers display
    ``plan -> impl -> verify -> judge -> done`` while existing yaml examples
    keep emitting ``review.approved`` / ``test.passed`` / ``judge.passed``.
    """

    event_items = [_event_obj(item) for item in events]
    event_types = [_event_type(event) for event in event_items]
    status = str(task.status or "").strip().lower().replace("-", "_")
    phase_value = str(phase or "").strip().lower().replace("-", "_")
    role = _role_prefix(task.assigned_to or "")
    judge_seen = any(event_type.startswith("judge.") for event_type in event_types)
    judge_enabled = judge_configured or judge_seen

    impl_gate_state = _latest_state(event_types, _IMPL_GATE_EVENTS)
    if impl_gate_state == "empty":
        impl_gate_state = "pending" if "dev.build.done" in event_types else "not_configured"

    verify_lanes = _verify_lanes(event_types)
    verify_state = _verify_rollup(event_types, verify_lanes, impl_gate_state)

    judge_state = _latest_state(event_types, _JUDGE_EVENTS)
    if judge_state == "empty":
        if judge_enabled:
            judge_state = "pending" if verify_state == "passed" else "waiting"
        else:
            judge_state = "not_configured"

    rework_event = _latest_event(event_items, _REWORK_EVENTS)
    rework_target = ""
    rework_reason = ""
    if rework_event is not None:
        payload = _event_payload(rework_event)
        rework_target = str(
            payload.get("rework_target")
            or payload.get("target_role")
            or payload.get("route_to")
            or "dev"
        )
        rework_reason = str(
            payload.get("reason")
            or payload.get("summary")
            or _event_type(rework_event)
        )

    workflow_phase = _workflow_phase(
        status=status,
        phase=phase_value,
        role=role,
        event_types=event_types,
        impl_gate_state=impl_gate_state,
        verify_state=verify_state,
        judge_state=judge_state,
        judge_configured=judge_enabled,
    )
    terminal_required = terminal_success_event or (
        "judge.passed" if judge_enabled else ""
    )
    return WorkflowProjection(
        workflow_phase=workflow_phase,
        impl_exit_gate_state=impl_gate_state,
        verify_state=verify_state,
        judge_state=judge_state,
        verify_lanes=verify_lanes,
        terminal_required_event=terminal_required,
        rework_target=rework_target,
        rework_reason=rework_reason,
        badges=_workflow_badges(
            impl_gate_state=impl_gate_state,
            verify_state=verify_state,
            judge_state=judge_state,
            rework_target=rework_target,
        ),
    )


def _projection(
    column: str,
    reason: str,
    badges: list[str],
) -> KanbanColumnProjection:
    return KanbanColumnProjection(
        column=column,
        label=kanban_column_label(column),
        reason=reason,
        badges=tuple(dict.fromkeys(badges)),
    )


def _role_prefix(assigned_to: str) -> str:
    value = assigned_to.strip().lower()
    if not value:
        return ""
    for sep in ("-", "_", "."):
        value = value.split(sep, 1)[0]
    return value


def _workflow_reason(role: str, phase: str) -> str:
    if role and phase:
        return f"role:{role};phase:{phase}"
    if role:
        return f"role:{role}"
    if phase:
        return f"phase:{phase}"
    return "workflow:in_progress"


def _event_obj(item: object) -> object:
    if isinstance(item, tuple) and item:
        return item[-1]
    return item


def _event_type(event: object) -> str:
    return str(getattr(event, "type", "") or "")


def _event_payload(event: object) -> dict[str, Any]:
    payload = getattr(event, "payload", {}) or {}
    return payload if isinstance(payload, dict) else {}


def _latest_state(event_types: list[str], mapping: dict[str, str]) -> str:
    for event_type in reversed(event_types):
        state = mapping.get(event_type)
        if state:
            return state
    return "empty"


def _latest_event(events: list[object], event_types: set[str]) -> object | None:
    for event in reversed(events):
        if _event_type(event) in event_types:
            return event
    return None


def _verify_lanes(event_types: list[str]) -> tuple[dict[str, str], ...]:
    latest: dict[str, dict[str, str]] = {}
    for event_type in event_types:
        lane_state = _VERIFY_LANE_EVENTS.get(event_type)
        if lane_state is None:
            continue
        lane, state = lane_state
        latest[lane] = {
            "lane": lane,
            "state": state,
            "event_type": event_type,
        }
    return tuple(latest[lane] for lane in sorted(latest))


def _verify_rollup(
    event_types: list[str],
    lanes: tuple[dict[str, str], ...],
    impl_gate_state: str,
) -> str:
    if any(lane.get("state") in {"failed", "blocked"} for lane in lanes):
        return "failed"
    if "verify.passed" in event_types or "test.passed" in event_types:
        return "passed"
    if "review.approved" in event_types:
        return "partial"
    if impl_gate_state in {"passed", "skipped"} or "dev.build.done" in event_types:
        return "pending"
    return "empty"


def _workflow_phase(
    *,
    status: str,
    phase: str,
    role: str,
    event_types: list[str],
    impl_gate_state: str,
    verify_state: str,
    judge_state: str,
    judge_configured: bool,
) -> str:
    if status in _DONE_STATUSES:
        return "done"
    if status == "blocked":
        return "blocked"
    if status in _TODO_STATUSES:
        return "plan"
    latest_workflow_event = next(
        (event_type for event_type in reversed(event_types) if event_type in _WORKFLOW_EVENTS),
        "",
    )
    if latest_workflow_event in _REWORK_EVENTS:
        return "impl"
    if judge_state in {"pending", "passed", "failed"} and judge_configured:
        return "judge"
    if verify_state in {"pending", "partial", "passed", "failed"}:
        return "verify"
    if impl_gate_state in {"passed", "skipped"}:
        return "verify"
    if phase.startswith("design") or role in {"arch", "critic", "plan", "planner"}:
        return "plan"
    if role in _VERIFY_ROLE_PREFIXES:
        return "judge" if role == "judge" else "verify"
    return "impl"


def _workflow_badges(
    *,
    impl_gate_state: str,
    verify_state: str,
    judge_state: str,
    rework_target: str,
) -> tuple[dict[str, str], ...]:
    badges: list[dict[str, str]] = []
    if impl_gate_state not in {"empty", "not_configured"}:
        badges.append(_badge("impl gate", impl_gate_state))
    if verify_state != "empty":
        badges.append(_badge("verify", verify_state))
    if judge_state != "not_configured":
        badges.append(_badge("judge", judge_state))
    if rework_target:
        badges.append({
            "kind": "rework",
            "label": f"rework {rework_target}",
            "tone": "warn",
            "state": "requested",
        })
    return tuple(badges)


def _badge(kind: str, state: str) -> dict[str, str]:
    return {
        "kind": kind.replace(" ", "_"),
        "label": f"{kind} {state}",
        "tone": _badge_tone(state),
        "state": state,
    }


def _badge_tone(state: str) -> str:
    if state in {"passed", "skipped"}:
        return "ok"
    if state in {"failed", "rejected", "blocked"}:
        return "err"
    if state in {"partial", "pending", "waiting"}:
        return "warn"
    return "muted"
