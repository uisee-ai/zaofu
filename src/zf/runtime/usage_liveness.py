"""Project worker liveness from provider usage events."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry


def apply_agent_usage_liveness(
    registry: RoleSessionRegistry,
    event: ZfEvent,
    *,
    tasks: Iterable[Any] = (),
    events: Iterable[ZfEvent] = (),
) -> None:
    """Treat provider usage as worker activity for stuck detection.

    Long provider turns can emit usage/tool activity without an explicit
    worker heartbeat. Recording this as a heartbeat-like liveness sample keeps
    the sweep from reporting a false stuck worker while preserving the normal
    heartbeat protocol as the primary worker signal.
    """
    instance_id = str(event.actor or "").strip()
    if not instance_id:
        return
    payload = event.payload if isinstance(event.payload, dict) else {}
    explicit_task_id = str(event.task_id or payload.get("task_id") or "").strip()
    fanout_events = list(events)
    if (
        explicit_task_id
        and _fanout_task_state(instance_id, explicit_task_id, fanout_events)
        == "terminal"
    ):
        explicit_task_id = ""
    task_id = explicit_task_id
    previous_state = ""
    previous_task_id = ""
    previous_source = ""
    if not explicit_task_id:
        _, previous = registry.get_last_heartbeat(instance_id)
        if isinstance(previous, dict):
            previous_state = str(previous.get("state") or "").strip().lower()
            previous_task_id = str(
                previous.get("current_task_id") or previous.get("task_id") or ""
            ).strip()
            previous_source = str(previous.get("source") or "").strip()
    if not task_id:
        task_id = _active_task_for_instance(
            instance_id,
            tasks,
            events=fanout_events,
        )
    state = "busy" if task_id else "active"
    if not explicit_task_id:
        if previous_state in {"idle", "awaiting_review"} and (
            not task_id or not previous_task_id or task_id == previous_task_id
        ):
            state = previous_state
            task_id = previous_task_id or task_id
        elif (
            previous_task_id
            and previous_source in {
                "task.dispatched",
                "fanout.child.dispatched",
                "worker.state.changed",
            }
            and task_id != previous_task_id
            and _fanout_task_state(instance_id, previous_task_id, fanout_events)
            != "terminal"
        ):
            # Writer fanout keeps canonical tasks active until candidate
            # verify/judge, so multiple tasks may be assigned to the same
            # lane. The latest dispatch/current-state projection is more
            # precise than a TaskStore scan in that case.
            task_id = previous_task_id
            if previous_state in {"busy", "pending_recycle", "recycling"}:
                state = previous_state
    context_ratio = (
        payload.get("context_usage_ratio")
        if payload.get("context_usage_ratio") is not None
        else payload.get("ratio")
    )
    registry.record_heartbeat(
        instance_id,
        {
            "instance_id": instance_id,
            "state": state,
            "current_task_id": task_id,
            "last_action_ts": event.ts,
            "context_usage_ratio": context_ratio,
            "source": "agent.usage",
            "event_id": event.id,
        },
    )


def _active_task_for_instance(
    instance_id: str,
    tasks: Iterable[Any],
    *,
    events: Iterable[ZfEvent] = (),
) -> str:
    fanout_events = list(events)
    for task in tasks:
        if str(getattr(task, "assigned_to", "") or "") != instance_id:
            continue
        if str(getattr(task, "status", "") or "") not in {
            "in_progress",
            "review",
            "testing",
        }:
            continue
        task_id = str(getattr(task, "id", "") or "")
        if _fanout_task_state(instance_id, task_id, fanout_events) == "terminal":
            continue
        return task_id
    return ""


def _fanout_task_state(
    instance_id: str,
    task_id: str,
    events: Iterable[ZfEvent],
) -> str:
    """Latest fanout lifecycle state for a task on a role instance.

    Canonical writer tasks can remain ``in_progress`` while the writer child is
    already terminal and downstream verify/judge owns the delivery. Provider
    usage samples must not resurrect that terminal child as the lane's current
    task just because TaskStore still keeps the canonical task active.
    """
    if not instance_id or not task_id:
        return ""
    event_list = events if isinstance(events, list) else list(events)
    terminal_fanouts: set[str] = set()
    for event in reversed(event_list):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in {"fanout.cancelled", "fanout.timed_out"}:
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id:
                terminal_fanouts.add(fanout_id)
            continue
        if event.type not in {
            "fanout.child.dispatched",
            "fanout.child.completed",
            "fanout.child.failed",
            "fanout.child.dispatch_lost",
        }:
            continue
        role_instance = str(payload.get("role_instance") or "").strip()
        if role_instance != instance_id:
            continue
        event_task_id = str(event.task_id or payload.get("task_id") or "").strip()
        if event_task_id != task_id:
            continue
        if event.type == "fanout.child.dispatch_lost":
            return "terminal"
        if event.type == "fanout.child.dispatched":
            if str(payload.get("fanout_id") or "") in terminal_fanouts:
                return "terminal"
            return "active"
        return "terminal"
    return ""
