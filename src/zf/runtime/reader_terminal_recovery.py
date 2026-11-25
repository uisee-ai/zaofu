"""Converge exhausted reader dispatches without inventing semantic results."""

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_runtime import workflow_operation_service
from zf.runtime.workflow_operation import reduce_workflow_operations


def reconcile_exhausted_reader_operations(runtime: Any, events: list[ZfEvent]) -> bool:
    from zf.runtime.reader_wait_recovery import reconcile_undelivered_wait_failures

    if reconcile_undelivered_wait_failures(runtime, events):
        return True
    failures = [
        e
        for e in events
        if (e.type == "fanout.child.failed" and e.origin == "kernel"
            and e.payload.get("failure_class") == "worker_noop_or_terminal_missing")
        or (e.type == "fanout.synth.completed" and e.payload.get("status") == "failed"
            and (e.origin == "kernel" or not e.origin and e.actor == "zf-cli")
            and (e.payload.get("reason") == "synth_timeout"
                 or e.payload.get("failure_class") == "worker_noop_or_terminal_missing"))
    ]
    if not failures:
        return False
    operations = reduce_workflow_operations(events)
    positions = {e.id: i for i, e in enumerate(events)}
    dispatches = {}
    for event in events:
        if event.type in {"fanout.child.dispatched", "fanout.synth.dispatched"}:
            p = event.payload
            child = "synth" if event.type == "fanout.synth.dispatched" else p.get("child_id")
            dispatches[(p.get("fanout_id"), child)] = event
    for failed in reversed(failures):
        p = failed.payload
        is_synth = failed.type == "fanout.synth.completed"
        child = "synth" if is_synth else p.get("child_id")
        dispatched = dispatches.get((p.get("fanout_id"), child))
        if dispatched is None or positions[dispatched.id] > positions[failed.id]:
            continue
        if p.get("run_id") != dispatched.payload.get("run_id"):
            continue
        op = operations.get(str(dispatched.payload.get("operation_id") or ""))
        if not op or op.get("operation_type") != ("fanout_synth" if is_synth else "fanout_reader_child"):
            continue
        if is_synth and (
            op.get("request_hash") != dispatched.payload.get("request_hash")
            or op.get("active_attempt_id") != (dispatched.payload.get("attempt_id") or dispatched.payload.get("run_id"))
        ):
            continue
        if op.get("status") not in {"running", "requested", "reserved", "suspended"}:
            continue
        if positions.get(str(op.get("last_event_id") or ""), -1) > positions[failed.id]:
            continue
        service = workflow_operation_service(runtime)
        settle = service.fail if is_synth else service.block
        closed = settle(
            operation_id=op["operation_id"],
            request_hash=op["request_hash"],
            workflow_run_id=op["workflow_run_id"],
            task_id=str(op.get("task_id") or ""),
            reason=str(p.get("reason") or "reader recovery exhausted"),
            causation_id=failed.id,
            correlation_id=op["workflow_run_id"],
            details={
                "failure_class": p.get("failure_class") or "reader_execution_failure",
                "fanout_id": p.get("fanout_id"),
                "child_id": child,
            },
        )
        if closed is not None:
            return True
    return False
