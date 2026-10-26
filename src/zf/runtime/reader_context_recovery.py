"""Keep reader context recovery inside the owning fanout lifecycle."""

from typing import Any

from zf.core.events.model import ZfEvent


def reader_fanout_owns_context_recovery(runtime: Any, event: ZfEvent) -> bool:
    """Resolve both live critical events and pre-upgrade retry schedules.

    Match the original kernel dispatch, including after its attempt has ended.
    A late reader schedule must never reopen its product's implementation Task.
    """
    events = runtime.event_log.read_all()
    if event.type in {"task.retry_scheduled", "task.continuation_scheduled"}:
        trigger_id = str(event.payload.get("trigger_event_id") or "")
        trigger = next((item for item in reversed(events) if item.id == trigger_id), None)
        if trigger is None or trigger.task_id != event.task_id:
            return False
        event = trigger
    if event.type != "worker.context.critical" or event.origin != "kernel":
        return False
    scope = event.payload.get("active_fanout")
    if not isinstance(scope, dict) or not all(scope.get(k) for k in (
        "fanout_id", "stage_id", "child_id", "run_id",
    )):
        return False
    manifest = runtime._fanout_manifest(str(scope["fanout_id"]))
    if not manifest or manifest.get("topology") != "fanout_reader":
        return False
    for dispatch in reversed(events):
        if dispatch.type != "fanout.child.dispatched" or dispatch.origin != "kernel":
            continue
        payload = dispatch.payload
        if (
            all(payload.get(key) == scope[key] for key in (
                "fanout_id", "stage_id", "child_id", "run_id",
            ))
            and payload.get("role_instance") == event.actor
            and (dispatch.task_id or payload.get("task_id")) == event.task_id
            and dispatch.correlation_id == event.correlation_id
            and event.payload.get("dispatch_id") == scope["run_id"]
        ):
            return True
    return False
