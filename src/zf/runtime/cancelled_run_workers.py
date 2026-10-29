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
    # Re-read durable receipts: callers may carry a pre-stop sweep snapshot.
    events = runtime.event_log.read_all()
    worker_task = getattr(runtime, "_last_worker_task_id", {}).get(role_instance, "")
    if worker_task and worker_task != task_id:
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
        and event.payload.get("provider_stop_confirmed") is True
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
    from zf.runtime.workflow_operation import reduce_workflow_operations

    for operation in reduce_workflow_operations(events).values():
        if (operation.get("dispatch_id") == dispatch_id
                and operation.get("role_instance") == role_instance):
            context.update({key: operation.get(key, "") for key in (
                "operation_id", "request_hash", "active_attempt_id",
            )})
            break
    runtime._set_worker_state(
        role_instance, "cancelling", task_id=task_id, force=True,
        reason="cancelled dispatch stopping provider", extra_payload=context,
    )
    try:
        runtime.transport.terminate(role_instance)
        is_alive = getattr(runtime.transport, "is_alive", None)
        if not callable(is_alive) or is_alive(role_instance) is not False:
            raise RuntimeError("provider termination not confirmed")
        runtime._set_worker_state(
            role_instance, "idle", task_id=task_id, force=True,
            reason="cancelled dispatch stopped provider",
            extra_payload={**context, "provider_stop_confirmed": True},
            require_persisted=True,
        )
    except Exception as exc:
        runtime._run_cancel_resources_reconciled = False
        runtime._set_worker_state(
            role_instance, "blocked_human", task_id=task_id, force=True,
            reason=f"cancelled workflow provider stop failed: {exc}", extra_payload=context,
        )
        return False
    return True


def reconcile_pending_stops(runtime: Any, events: list[ZfEvent]) -> None:
    """Retry interrupted stops from the existing reconciliation entrypoint."""
    by_id = {event.id: event for event in events}
    pending: dict[tuple[str, str], ZfEvent] = {}
    for event in events:
        payload = event.payload
        if event.type != "worker.state.changed" or not payload.get("dispatch_id"):
            continue
        key = (event.actor, str(payload["dispatch_id"]))
        if payload.get("provider_stop_confirmed") is True:
            pending.pop(key, None)
        elif payload.get("source") in {"run_cancel_reconciliation", "superseded_reader_fanout_closeout"}:
            pending[key] = event
    for (role, dispatch), event in pending.items():
        payload = event.payload
        cancellation = by_id.get(payload.get("fanout_cancelled_event_id") or payload.get("run_cancelled_event_id"))
        if cancellation is not None:
            stop_cancelled_dispatch(
                runtime, role_instance=role, dispatch_id=dispatch,
                task_id=str(event.task_id or ""), events=events, cancellation=cancellation,
            )
