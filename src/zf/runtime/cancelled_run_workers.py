"""Stop only the latest dispatch still owned by a cancelled run or fanout."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent


def stop_cancelled_dispatch(
    runtime: Any,
    *,
    role_instance: str,
    task_id: str,
    dispatch_id: str,
    events: list[ZfEvent],
    cancellation: ZfEvent,
) -> bool:
    source = (
        "superseded_reader_fanout_closeout"
        if cancellation.type == "fanout.cancelled" else "run_cancel_reconciliation"
    )
    if not role_instance or not dispatch_id:
        return False
    worker_task = getattr(runtime, "_last_worker_task_id", {}).get(role_instance, "")
    if worker_task and worker_task != task_id:
        return False
    if (
        not worker_task
        and getattr(runtime, "_last_worker_state", {}).get(role_instance) == "idle"
    ):
        return False
    latest_dispatch = None
    for event in reversed(events):
        if event.type not in {"fanout.child.dispatched", "task.dispatched", "workflow.operation.started"}:
            continue
        payload = event.payload or {}
        instance = payload.get("role_instance") or payload.get("assignee")
        if instance == role_instance:
            latest_dispatch = str(payload.get("dispatch_id") or payload.get("run_id") or "")
            if not latest_dispatch:
                continue
            break
    if latest_dispatch != dispatch_id:
        return False
    if any(
        event.type == "worker.state.changed"
        and event.actor == role_instance
        and event.payload.get("to") == "idle"
        and event.payload.get("source") == source
        and event.payload.get("dispatch_id") == dispatch_id
        for event in events
    ):
        return False
    task = runtime.task_store.get(task_id) if task_id else None
    if (
        task is not None
        and task.assigned_to == role_instance
        and task.active_dispatch_id
        and task.active_dispatch_id != dispatch_id
    ):
        return False
    context = {
        "source": source,
        "dispatch_id": dispatch_id,
        ("fanout_cancelled_event_id" if cancellation.type == "fanout.cancelled"
         else "run_cancelled_event_id"): cancellation.id,
    }
    runtime._set_worker_state(
        role_instance, "cancelling", task_id=task_id, force=True,
        reason="cancelled dispatch stopping provider", extra_payload=context,
    )
    try:
        runtime.transport.terminate(role_instance)
        is_alive = getattr(runtime.transport, "is_alive", None)
        if callable(is_alive) and is_alive(role_instance):
            raise RuntimeError("provider still alive after termination")
    except Exception as exc:
        runtime._set_worker_state(
            role_instance, "blocked_human", task_id=task_id, force=True,
            reason=f"cancelled workflow provider stop failed: {exc}", extra_payload=context,
        )
        return False
    runtime._set_worker_state(
        role_instance, "idle", task_id=task_id, force=True,
        reason="cancelled dispatch stopped provider", extra_payload=context,
    )
    return True
