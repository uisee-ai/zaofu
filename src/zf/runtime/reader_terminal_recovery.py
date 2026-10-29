"""Converge exhausted reader dispatches without inventing semantic results."""

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_runtime import workflow_operation_service
from zf.runtime.workflow_operation import reduce_workflow_operations


def reconcile_exhausted_reader_operations(runtime: Any, events: list[ZfEvent]) -> bool:
    failures = [
        e
        for e in events
        if e.type == "fanout.child.failed"
        and e.origin == "kernel"
        and e.payload.get("failure_class") == "worker_noop_or_terminal_missing"
    ]
    if not failures:
        return False
    operations = reduce_workflow_operations(events)
    positions = {e.id: i for i, e in enumerate(events)}
    dispatches = {}
    for event in events:
        if event.type == "fanout.child.dispatched":
            p = event.payload
            dispatches[(p.get("fanout_id"), p.get("child_id"))] = event
    for failed in reversed(failures):
        p = failed.payload
        dispatched = dispatches.get((p.get("fanout_id"), p.get("child_id")))
        if dispatched is None or positions[dispatched.id] > positions[failed.id]:
            continue
        if p.get("run_id") != dispatched.payload.get("run_id"):
            continue
        op = operations.get(str(dispatched.payload.get("operation_id") or ""))
        if not op or op.get("operation_type") != "fanout_reader_child":
            continue
        if op.get("status") not in {"running", "requested", "reserved", "suspended"}:
            continue
        if positions.get(str(op.get("last_event_id") or ""), -1) > positions[failed.id]:
            continue
        workflow_operation_service(runtime).block(
            operation_id=op["operation_id"],
            request_hash=op["request_hash"],
            workflow_run_id=op["workflow_run_id"],
            task_id=str(op.get("task_id") or ""),
            reason=str(p.get("reason") or "reader recovery exhausted"),
            causation_id=failed.id,
            correlation_id=op["workflow_run_id"],
            details={
                "failure_class": "worker_noop_or_terminal_missing",
                "fanout_id": p.get("fanout_id"),
                "child_id": p.get("child_id"),
            },
        )
        return True
    return False
