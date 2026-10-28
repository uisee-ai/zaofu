"""Canonical worker.stuck signal construction for recovery entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry


@dataclass(frozen=True)
class StuckSignal:
    reason: str
    task: Any
    dispatch_id: str
    event: ZfEvent


def emit_stuck_signal(
    runtime: Any,
    role: Any,
    *,
    trigger_event: ZfEvent | None,
    source: str,
    reason: str,
    heartbeat_age_s: float | None,
) -> StuckSignal:
    reason = reason or (
        f"worker {role.instance_id} produced no new output for "
        f"{role.stuck_threshold_seconds:.0f}s"
    )
    task = runtime._active_task_for_instance(role.instance_id)
    dispatch = (
        runtime._latest_dispatch_event_for_task(task.id) if task is not None else None
    )
    foreign_dispatch = False
    if dispatch is not None and dispatch.type == "fanout.child.dispatched":
        dispatch_role = str(dispatch.payload.get("role_instance") or "")
        if dispatch_role and dispatch_role != role.instance_id:
            foreign_dispatch = True
            dispatch = None
    dispatch_payload = dispatch.payload if dispatch is not None else {}
    task_dispatch_id = (
        getattr(task, "active_dispatch_id", "") if task and not foreign_dispatch else ""
    )
    dispatch_id = task_dispatch_id
    briefing = ""
    if isinstance(dispatch_payload, dict):
        dispatch_id = str(dispatch_payload.get("dispatch_id") or task_dispatch_id or "")
        briefing = str(dispatch_payload.get("briefing") or "")

    causal_event = trigger_event or dispatch
    session_id = _role_session_id(runtime, role.instance_id)
    event = ZfEvent(
        type="worker.stuck",
        actor=role.instance_id,
        task_id=task.id if task is not None else None,
        causation_id=causal_event.id if causal_event is not None else None,
        correlation_id=(
            causal_event.correlation_id if causal_event is not None else None
        ),
        payload={
            "role": role.name,
            "instance_id": role.instance_id,
            "threshold_seconds": role.stuck_threshold_seconds,
            "task_id": task.id if task is not None else "",
            "dispatch_id": dispatch_id,
            "briefing": briefing,
            "pane_current_command": runtime._pane_current_command(role.instance_id),
            "role_session_id": session_id,
            "source": source,
            "reason": reason,
            "trigger_event_id": (causal_event.id if causal_event is not None else ""),
            "heartbeat_age_s": (
                round(heartbeat_age_s, 1) if heartbeat_age_s is not None else None
            ),
        },
    )
    try:
        event = runtime.event_writer.append(event)
    except Exception:
        pass
    runtime._set_worker_state(
        role.instance_id,
        "stuck",
        reason=f"no output for {role.stuck_threshold_seconds:.0f}s",
    )
    return StuckSignal(
        reason=reason,
        task=task,
        dispatch_id=dispatch_id,
        event=event,
    )


def _role_session_id(runtime: Any, instance_id: str) -> str:
    try:
        registry = RoleSessionRegistry(
            runtime.state_dir / "role_sessions.yaml",
            project_root=str(runtime.project_root),
        )
        cached = registry.get(instance_id)
        return str(cached) if cached else ""
    except Exception:
        return ""


__all__ = ["StuckSignal", "emit_stuck_signal"]
