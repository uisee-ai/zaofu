"""Select cost-monitoring dispatches that have no bound terminal fact."""

from collections.abc import Sequence

from zf.core.events.model import ZfEvent


def _task_operation(event: ZfEvent) -> tuple[str, ...]:
    body = event.payload
    task = event.task_id or body.get("task_id")
    if body.get("task_id") and task != body["task_id"]:
        return ()
    key = (body.get("workflow_run_id"), task, body.get("operation_id"), body.get("request_hash"))
    return key if all(isinstance(value, str) and value for value in key) else ()


def _result_ref(event: ZfEvent, field: str) -> tuple[str, str] | None:
    ref = event.payload.get(field)
    if isinstance(ref, dict) and all(isinstance(ref.get(k), str) and ref[k] for k in ("ref", "sha256")):
        return ref["ref"], ref["sha256"]
    return None


def last_open_cost_dispatch(events: Sequence[ZfEvent]) -> ZfEvent | None:
    closed_readers: set[tuple[str, ...]] = set()
    closed_tasks: set[tuple[str, ...]] = set()
    settlements = {}
    fields = ("workflow_run_id", "fanout_id", "child_id", "run_id")
    for event in reversed(events):
        body = event.payload or {}
        if event.type == "workflow.operation.settled" and event.origin == "kernel" and event.causation_id:
            key, ref = _task_operation(event), _result_ref(event, "admitted_call_result_ref")
            if key and ref:
                settlements[event.causation_id] = (key, ref)
        elif event.type == "workflow.call.result.admitted" and event.id in settlements:
            # Settlement has no attempt ID; use only the kernel admission for the same result envelope.
            key, ref = settlements.pop(event.id)
            if (
                event.origin == "kernel"
                and body.get("admission_status") == "admitted"
                and _task_operation(event) == key
                and _result_ref(event, "envelope_ref") == ref
                and all(isinstance(body.get(k), str) and body[k] for k in ("attempt_id", "dispatch_id"))
            ):
                closed_tasks.add((*key, body["attempt_id"], body["dispatch_id"]))
        elif event.type == "task.dispatched":
            task = (*_task_operation(event), body.get("attempt_id"), body.get("dispatch_id"))
            if (
                event.origin != "kernel"
                or not all(isinstance(value, str) for value in task)
                or task not in closed_tasks
            ):
                return event
        elif event.type in {"fanout.child.completed", "fanout.child.failed", "fanout.child.dispatched"}:
            identity = tuple(str(body.get(key) or "") for key in fields)
            if event.type == "fanout.child.dispatched":
                if identity not in closed_readers:
                    return event
            elif event.origin == "kernel" and all(identity):
                closed_readers.add(identity)
    return None
