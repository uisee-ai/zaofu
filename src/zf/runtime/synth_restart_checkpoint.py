"""Explicitly reopen an exact synth transport failure under the respawn lease."""
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash, reduce_workflow_operations


def reopen_synth_restart_checkpoint(runtime: Any, role: Any, failure_id: str) -> None:
    """Keep the call/reads intact; never reopen a semantic or admitted result.

    The caller holds the same per-role lease as provider replacement. Existing
    recovery injection binds the reopened dispatch before that lease is released.
    """
    events = runtime.event_log.read_all()
    by_id = {event.id: event for event in events}
    failure = by_id.get(failure_id)
    if failure is None or failure.type != "fanout.synth.completed":
        raise ValueError("an exact fanout.synth.completed failure event is required")
    p = failure.payload
    if (failure.origin != "kernel" or failure.actor != "zf-cli"
            or p.get("status") != "failed" or p.get("report")
            or p.get("failure_class") != "worker_noop_or_terminal_missing"
            or p.get("reason") not in {
                "provider_turn_closed_without_synth_result_recovery_exhausted",
                "reader_synth_session_replaced_after_dispatch_recovery_exhausted",
            } or p.get("role_instance") != role.instance_id):
        raise ValueError("only a kernel transport failure without a review result may resume")
    lost = by_id.get(failure.causation_id)
    if (lost is None or lost.type != "fanout.child.dispatch_lost"
            or lost.origin != "kernel" or lost.actor != "zf-cli"
            or lost.payload.get("child_id") != "synth"
            or lost.payload.get("semantic_attempt_consumed") is not False
            or any(lost.payload.get(key) != p.get(key)
                   for key in ("fanout_id", "run_id", "role_instance"))):
        raise ValueError("failure has no matching non-semantic dispatch loss")
    fanout_id = str(p.get("fanout_id") or "")
    dispatches = [event for event in events if event.type == "fanout.synth.dispatched"
                  and event.payload.get("fanout_id") == fanout_id]
    if not dispatches:
        raise ValueError("original synth dispatch is missing")
    dispatch = dispatches[-1]
    d = dispatch.payload
    if events.index(dispatch) >= events.index(failure):
        raise ValueError("failure already has a newer dispatch")
    if any(d.get(key) != p.get(key) for key in ("run_id", "role_instance", "stage_id")):
        raise ValueError("failure differs from the original dispatch")
    manifest = runtime._fanout_manifest(fanout_id)
    aggregate = manifest.get("aggregate") or {}
    aggregate_event = by_id.get(aggregate.get("last_event_id"))
    partial = bool(aggregate_event and aggregate_event.type == "fanout.aggregate.started"
                   and aggregate_event.origin == "kernel" and aggregate_event.actor == "zf-cli"
                   and aggregate_event.payload.get("recovery_kind") == "operator_synth_checkpoint"
                   and aggregate_event.payload.get("recovered_failure_event_id") == failure_id
                   and aggregate_event.causation_id == failure_id)
    if (manifest.get("topology") != "fanout_reader"
            or (manifest.get("synth") or {}).get("last_event_id") != failure_id
            or not (partial or (aggregate.get("status") == "failed"
                                and aggregate.get("synth_event_id") == failure_id))
            or runtime._fanout_identity_stale_reason(fanout_id)[0]):
        raise ValueError("failed synth carrier is no longer current")
    operation = reduce_workflow_operations(events).get(d.get("operation_id"), {})
    if (operation.get("operation_type") != "fanout_synth"
            or operation.get("status") != "running"
            or runtime._fanout_dispatch_has_authoritative_result(events, dispatch)):
        raise ValueError("operation is blocked, terminal, or already has an admitted result")
    run = build_run_admission_projection(events).runs.get(operation.get("workflow_run_id"))
    if run is None or run.status != "running":
        raise ValueError("synth resume requires a running admitted Run")
    for key in ("request_hash", "workflow_run_id", "role_instance"):
        if not d.get(key) or d.get(key) != operation.get(key):
            raise ValueError(f"dispatch {key} differs from the immutable operation")
    if d.get("run_id") != operation.get("active_attempt_id"):
        raise ValueError("attempt identity differs from the original dispatch")
    request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload
    if operation_request_hash(request) != operation["request_hash"]:
        raise ValueError("immutable request digest mismatch")
    if not Path(str(d.get("briefing_path") or "")).is_file():
        raise ValueError("original synth briefing is missing")
    if runtime._fanout_role_has_active_provider_turn(role.instance_id):
        raise ValueError("provider still has an active turn")
    recovery = {"recovery_kind": "operator_synth_checkpoint",
                "recovered_failure_event_id": failure_id,
                "semantic_attempt_consumed": False}
    if not partial:
        runtime.event_writer.append(ZfEvent(
            type="fanout.aggregate.started", actor="zf-cli", origin="kernel",
            payload={"fanout_id": fanout_id, "trace_id": operation["workflow_run_id"],
                     "stage_id": d["stage_id"], **recovery},
            causation_id=failure_id, correlation_id=operation["workflow_run_id"],
        ))
    runtime.event_writer.append(ZfEvent(
        type="fanout.synth.dispatched", actor="zf-cli", origin="kernel",
        task_id=dispatch.task_id, payload={**d, **recovery, "retry_of_event_id": dispatch.id},
        causation_id=failure_id, correlation_id=dispatch.correlation_id,
    ))
