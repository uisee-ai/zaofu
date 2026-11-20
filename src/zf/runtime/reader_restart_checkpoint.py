"""Operator recovery of a single reader lost before producing any result."""
import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.attempt_read_progress import required_read_progress
from zf.runtime.run_admission import RUN_ADMISSION_SCHEMA_VERSION, build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import (
    WorkflowOperationService, operation_request_hash, reduce_workflow_operations,
)

RECOVERY_KIND = "operator_reader_checkpoint"
_LOSS = "provider_turn_closed_without_child_result_recovery_exhausted"


def _identity(payload: dict, key: str) -> Any:
    nested = payload.get("payload")
    return payload.get(key) or (nested.get(key) if isinstance(nested, dict) else None)


def inspect_reader_restart_checkpoint(runtime: Any, role: Any, failure_id: str,
                                      approval_ref: str) -> dict:
    """Reject changed inputs, semantic outcomes, live work and unrelated failures."""
    events = runtime.event_log.read_all()
    by_id = {e.id: e for e in events}
    failure = by_id.get(failure_id)
    if failure is None or failure.type != "fanout.child.failed":
        raise ValueError("an exact fanout.child.failed event is required")
    p = failure.payload
    run_id = str(p.get("trace_id") or p.get("workflow_run_id") or "")
    root = Path(runtime.project_root).resolve()
    approval = (root / approval_ref).resolve()
    if not approval_ref or not approval.is_relative_to(root):
        raise ValueError("reader checkpoint requires a project-local approval artifact")
    approval_bytes = approval.read_bytes()
    body = json.loads(approval_bytes)
    if not isinstance(body, dict) or any(body.get(k) != v for k, v in {
        "action": "resume-reader-checkpoint", "failure_event_id": failure_id,
        "workflow_run_id": run_id,
    }.items()):
        raise ValueError("approval artifact does not bind this reader checkpoint")
    fanout_id, child_id = str(p.get("fanout_id") or ""), str(p.get("child_id") or "")
    starts = [e for e in events if e.type == "fanout.started"
              and e.payload.get("trace_id") == run_id
              and e.payload.get("stage_id") == p.get("stage_id")
              and e.payload.get("recovery_kind") != RECOVERY_KIND]
    original = next((e for e in starts if e.payload.get("fanout_id") == fanout_id), None)
    if original is None or not child_id or p.get("role_instance") != role.instance_id:
        raise ValueError("reader checkpoint has no matching original carrier")
    starts = starts[starts.index(original):]
    dispatches = [e for e in events if e.type == "fanout.child.dispatched"
                  and e.payload.get("fanout_id") == fanout_id
                  and e.payload.get("child_id") == child_id
                  and e.payload.get("recovery_kind") != RECOVERY_KIND]
    if not dispatches or events.index(dispatches[-1]) >= events.index(failure):
        raise ValueError("reader failure has a newer dispatch")
    dispatch = dispatches[-1]
    d = dispatch.payload
    if (dispatch.origin != "kernel" or dispatch.actor != "zf-cli"
            or any(d.get(key) != p.get(key) for key in ("run_id", "role_instance", "stage_id"))):
        raise ValueError("failure differs from the original reader dispatch")
    operations = reduce_workflow_operations(events)
    operation = operations.get(_identity(d, "operation_id"), {})
    own_retry = any(e.type == "workflow.operation.retry_started"
                    and e.origin == "kernel" and e.actor == "zf-cli"
                    and e.causation_id == failure_id
                    and e.payload.get("reason") == RECOVERY_KIND
                    and e.payload.get("operation_id") == operation.get("operation_id")
                    and e.payload.get("request_hash") == operation.get("request_hash") for e in events)
    if (operation.get("operation_type") != "fanout_reader_child"
            or operation.get("status") not in ({"running"} if own_retry else {"failed"})
            or (not own_retry and operation.get("retry_count"))
            or operation.get("workflow_run_id") != run_id
            or operation.get("role_instance") != role.instance_id
            or operation.get("request_hash") != _identity(d, "request_hash")
            or (not own_retry and operation.get("reason") != _LOSS)):
        raise ValueError("reader operation is not an unchanged transport checkpoint")
    if any((o.get("workflow_run_id") == run_id or o.get("role_instance") == role.instance_id)
           and o.get("status") in {
        "requested", "reserved", "running", "suspended",
    } and not (own_retry and o.get("operation_id") == operation.get("operation_id"))
           for o in operations.values()):
        raise ValueError("Run still has another live operation")
    if runtime._fanout_role_has_active_provider_turn(role.instance_id):
        raise ValueError("provider still has an active turn")
    checkpoint_request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload
    if operation_request_hash(checkpoint_request) != operation["request_hash"]:
        raise ValueError("immutable request digest mismatch")
    descriptor = _identity(d, "attempt_source_manifest")
    if not isinstance(descriptor, dict) or descriptor.get("sha256") != checkpoint_request["request"].get("source_manifest_digest"):
        raise ValueError("source manifest differs from the immutable request")
    source_manifest = hydrate_sidecar_ref(runtime.state_dir, descriptor).payload
    attempt_id = operation["active_attempt_id"]
    if source_manifest.get("attempt_id") != attempt_id:
        raise ValueError("source manifest belongs to another reader attempt")
    progress = required_read_progress(runtime.state_dir, events, attempt_id=attempt_id,
                                      operation_id=operation["operation_id"])
    if progress is None or progress <= 0:
        raise ValueError("reader checkpoint has no valid required-input progress")
    if not Path(str(d.get("briefing_path") or "")).is_file():
        raise ValueError("original reader briefing is missing")
    latest_failure = None
    affected_ids = {e.payload["fanout_id"] for e in starts}
    affected_ops = {str(_identity(e.payload, "operation_id") or "") for e in events
                    if e.type == "fanout.child.dispatched" and e.payload.get("fanout_id") in affected_ids}
    if any(e.type == "workflow.call.result.admitted" and e.payload.get("operation_id") in affected_ops
           for e in events):
        raise ValueError("reader chain already has an admitted result")
    for start in starts:
        s = start.payload
        children = s.get("expected_children") or []
        if (start.origin != "kernel" or s.get("topology") != "fanout_reader"
                or len(children) != 1 or children[0].get("child_id") != child_id
                or children[0].get("role_instance") != role.instance_id
                or s.get("target_ref") != original.payload.get("target_ref")):
            raise ValueError("only an unchanged single-reader carrier can resume")
        trigger = by_id.get(s.get("trigger_event_id"))
        baseline = by_id.get(original.payload.get("trigger_event_id"))
        if trigger is None or baseline is None or any(trigger.payload.get(k) != baseline.payload.get(k)
            for k in ("request_revision", "requirement_spec_digest", "workflow_generation",
                      "task_map_generation", "plan_artifact_package_digest", "effective_config_digest")):
            raise ValueError("reader recovery chain changed its inputs or generation")
        if latest_failure is not None and trigger.payload.get("rework_of") != latest_failure.id:
            raise ValueError("newer reader carrier is not a derived transport-failure retry")
        scoped = [e for e in events if e.payload.get("fanout_id") == s["fanout_id"]
                  and e.payload.get("recovery_kind") != RECOVERY_KIND]
        failures = [e for e in scoped if e.type == "fanout.child.failed"]
        if not failures or any(e.type in {"fanout.child.completed", "fanout.synth.dispatched"}
                               for e in scoped):
            raise ValueError("reader chain contains a result or lacks its transport failure")
        failed = failures[-1]
        loss = by_id.get(failed.payload.get("lost_signal_event_id"))
        if (failed.origin != "kernel" or failed.actor != "zf-cli"
                or failed.payload.get("failure_class") != "worker_noop_or_terminal_missing"
                or failed.payload.get("reason") != _LOSS
                or loss is None or loss.type != "provider.turn.closed"
                or loss.actor != role.instance_id or failed.causation_id != loss.id
                or loss.payload.get("provider_status") != "failed"
                or loss.payload.get("provider_stop_reason") != "transport_error"):
            raise ValueError("reader chain includes a non-transport failure")
        if start is original and failed.id != failure_id:
            raise ValueError("requested failure is no longer the reader checkpoint")
        synths = [e for e in scoped if e.type == "fanout.synth.completed"]
        if not synths:
            raise ValueError("reader chain has no terminal aggregate evidence")
        synth = synths[-1]
        if (synth.origin != "kernel" or synth.actor != "zf-cli"
                or synth.payload.get("status") != "failed"
                or synth.payload.get("failure_class") not in {
                    "plan_candidate_preflight", "reader_execution_failure"}):
            raise ValueError("reader chain includes a semantic review")
        if synth.payload.get("failure_class") == "plan_candidate_preflight":
            findings = (synth.payload.get("report") or {}).get("findings") or []
            if not findings or any(f.get("code") not in {
                "task_map_producer_missing", "required_plan_port_missing", "plan_markdown_producer_missing",
            } for f in findings):
                raise ValueError("Plan preflight contains findings beyond the missing producer")
        stage_failures = [e for e in scoped if e.type == (s.get("aggregate") or {}).get("failure_event")
                          and e.causation_id == synth.id]
        if not stage_failures:
            raise ValueError("reader chain lacks its causal stage failure")
        latest_failure = stage_failures[-1]
    run = build_run_admission_projection(events).runs.get(run_id)
    if run is None or run.status not in {"running", "paused", "blocked"}:
        raise ValueError("reader checkpoint Run is not recoverable")
    escalation = None
    if run.status == "blocked":
        escalation = next((e for e in reversed(events) if e.type == "human.escalate"
            and e.payload.get("source_event_id") == latest_failure.id
            and e.payload.get("failure_class") == "stage_replan_cap_exhausted"), None)
        terminal = by_id.get(run.terminal_event_id)
        if (escalation is None or terminal is None
                or terminal.type != "run.goal.blocked"
                or terminal.causation_id != escalation.id):
            raise ValueError("blocked Run is not caused by this reader failure chain")
    return {"events": events, "failure": failure, "original": original, "dispatch": dispatch,
            "operation": operation, "run_status": run.status, "escalation": escalation,
            "approval_sha256": hashlib.sha256(approval_bytes).hexdigest(),
            "latest_failure": latest_failure, "progress": progress, "own_retry": own_retry,
            "superseded_fanout_ids": sorted(affected_ids - {fanout_id})}


def reopen_reader_restart_checkpoint(runtime: Any, role: Any, failure_id: str,
                                     approval_ref: str) -> None:
    checkpoint = inspect_reader_restart_checkpoint(runtime, role, failure_id, approval_ref)
    events, original = checkpoint["events"], checkpoint["original"]
    dispatch, operation = checkpoint["dispatch"], checkpoint["operation"]
    run_id = operation["workflow_run_id"]
    recovery = {"recovery_kind": RECOVERY_KIND, "recovered_failure_event_id": failure_id,
                "approval_ref": approval_ref, "semantic_attempt_consumed": False,
                "approval_sha256": checkpoint["approval_sha256"],
                "superseded_fanout_ids": checkpoint["superseded_fanout_ids"]}

    def append_once(event_type: str, payload: dict):
        existing = next((e for e in events if e.type == event_type
            and e.payload.get("recovery_kind") == RECOVERY_KIND
            and e.payload.get("recovered_failure_event_id") == failure_id), None)
        if existing:
            return existing
        return runtime.event_writer.append(ZfEvent(type=event_type, actor="zf-cli", origin="kernel",
            task_id=dispatch.task_id, payload={**payload, **recovery},
            causation_id=failure_id, correlation_id=run_id))

    append_once("fanout.started", dict(original.payload))
    append_once("fanout.aggregate.started", {"fanout_id": original.payload["fanout_id"],
        "trace_id": run_id, "stage_id": original.payload["stage_id"],
        "mode": (original.payload.get("aggregate") or {}).get("mode", "wait_for_all")})
    if not checkpoint["own_retry"]:
        WorkflowOperationService(state_dir=runtime.state_dir, event_log=runtime.event_log,
            event_writer=runtime.event_writer).mark_retry_started(
                operation_id=operation["operation_id"], request_hash=operation["request_hash"],
                workflow_run_id=run_id, retry_attempt=1, reason=RECOVERY_KIND,
                dispatch_id=dispatch.payload["run_id"], role_instance=role.instance_id,
                active_attempt_id=operation["active_attempt_id"], lease_id=operation["lease_id"],
                task_id=operation["task_id"], causation_id=failure_id, correlation_id=run_id)
    goal_update = next((e for e in events if e.type == "run.goal.updated"
        and e.payload.get("recovery_kind") == RECOVERY_KIND
        and e.payload.get("recovered_failure_event_id") == failure_id), None)
    if checkpoint["run_status"] == "blocked":
        goal_update = append_once("run.goal.updated", {"schema_version": "run-goal.update.v1", "run_id": run_id,
            "workflow_run_id": run_id, "status": "active", "reason": RECOVERY_KIND,
            "source_failure_event_id": checkpoint["latest_failure"].id,
            "stage_replan_generation_source_event_id": checkpoint["escalation"].id})
    elif checkpoint["run_status"] == "paused":
        append_once("run.resumed", {"schema_version": RUN_ADMISSION_SCHEMA_VERSION,
            "run_id": run_id, "workflow_run_id": run_id, "request_id": run_id, "reason": RECOVERY_KIND})
    if goal_update is not None:
        from zf.core.task.store import TaskStore
        from zf.runtime.workflow_task_lifecycle import reopen_workflow_managed_task_from_run_recovery

        reopen_workflow_managed_task_from_run_recovery(
            task_store=TaskStore(runtime.state_dir / "kanban.json"),
            event_writer=runtime.event_writer, recovery_event=goal_update)
    # Dispatch must belong to the reopened Run epoch for lifecycle recovery.
    append_once("fanout.child.dispatched", {**dispatch.payload, "retry_of_event_id": dispatch.id,
        "operation_id": operation["operation_id"], "request_hash": operation["request_hash"],
        "read_progress_attempt_id": operation["active_attempt_id"],
        "input_read_progress": checkpoint["progress"]})
