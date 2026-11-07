"""Settle never-started synth calls whose owning fanout failed in the kernel."""

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_runtime import workflow_operation_service
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def reconcile_failed_pending_synth(runtime: Any, events: list[ZfEvent]) -> bool:
    aggregates: dict[str, ZfEvent] = {}
    synths: dict[str, ZfEvent] = {}
    dispatched: set[str] = set()
    positions = {e.id: i for i, e in enumerate(events)}
    for event in events:
        fanout_id = str(event.payload.get("fanout_id") or "")
        if event.type in {"fanout.aggregate.started", "fanout.aggregate.completed"}:
            aggregates[fanout_id] = event
        elif event.type == "fanout.synth.completed":
            synths[fanout_id] = event
        elif event.type == "fanout.synth.dispatched":
            dispatched.add(fanout_id)
    if not aggregates:
        return False
    for op in reduce_workflow_operations(events).values():
        if (op.get("operation_type") != "fanout_synth"
                or op.get("status") != "requested"
                or op.get("started_event_id") or op.get("dispatch_id")):
            continue
        descriptor = op.get("request_ref")
        if not isinstance(descriptor, dict) or not descriptor:
            continue
        try:
            body = hydrate_sidecar_ref(runtime.state_dir, descriptor).payload
        except SidecarRefError:
            continue
        if not isinstance(body, dict) or not isinstance(body.get("request"), dict):
            continue
        fanout_id = str(body["request"].get("fanout_id") or "")
        terminal, failure = aggregates.get(fanout_id), synths.get(fanout_id)
        if not fanout_id or fanout_id in dispatched or terminal is None or failure is None:
            continue
        if terminal.type != "fanout.aggregate.completed":
            continue
        if any(e.origin != "kernel" or e.payload.get("status") != "failed"
               for e in (terminal, failure)):
            continue
        if (failure.payload.get("run_id") != op.get("active_attempt_id")
                or failure.payload.get("role_instance") != op.get("role_instance")):
            continue
        if any(
            str(e.payload.get("workflow_run_id") or e.payload.get("trace_id")
                or e.correlation_id or "") != op.get("workflow_run_id")
            or e.payload.get("stage_id") != op.get("parent_stage_id")
            for e in (terminal, failure)
        ):
            continue
        if not (positions.get(str(op.get("last_event_id") or ""), len(events))
                < positions[failure.id] < positions[terminal.id]):
            continue
        workflow_operation_service(runtime).supersede(
            operation_id=op["operation_id"], request_hash=op["request_hash"],
            workflow_run_id=op["workflow_run_id"],
            task_id=str(op.get("task_id") or ""),
            reason="owning fanout failed before synthesis dispatch",
            causation_id=terminal.id, correlation_id=op["workflow_run_id"],
        )
        return True
    return False
