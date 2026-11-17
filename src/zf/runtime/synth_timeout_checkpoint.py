"""Bind explicit synth timeout recovery to its exact Run and retained reads."""
import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.attempt_read_progress import required_read_progress
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


def inspect_synth_timeout(runtime: Any, *, events: list, failure: Any, dispatch: Any,
                          operation: dict, run: Any, approval_ref: str) -> dict:
    """Inspect only; every check must pass before any recovery event is written."""
    root = Path(runtime.project_root).resolve()
    path = (root / approval_ref).resolve()
    if not approval_ref or not path.is_relative_to(root):
        raise ValueError("synth timeout requires a project-local approval artifact")
    approval_bytes = path.read_bytes()
    approval = json.loads(approval_bytes)
    run_id = operation["workflow_run_id"]
    if not isinstance(approval, dict) or any(approval.get(k) != v for k, v in {
        "action": "resume-synth-checkpoint", "failure_event_id": failure.id,
        "workflow_run_id": run_id,
    }.items()):
        raise ValueError("approval artifact does not bind this synth timeout")
    if run is None or run.status not in {"running", "blocked"}:
        raise ValueError("synth timeout Run is not recoverable")
    d = dispatch.payload
    if any(e.type == "fanout.started" and e.payload.get("stage_id") == d["stage_id"]
           and (e.payload.get("workflow_run_id") or e.payload.get("trace_id")) == run_id
           and e.payload.get("fanout_id") != d["fanout_id"]
           for e in events[events.index(dispatch) + 1:]):
        raise ValueError("synth timeout has a newer stage carrier")
    if any(o.get("role_instance") == operation["role_instance"]
           and o.get("operation_id") != operation["operation_id"]
           and o.get("status") in {"requested", "reserved", "running", "suspended"}
           for o in reduce_workflow_operations(events).values()):
        raise ValueError("another operation owns this synth role")
    descriptor = d.get("attempt_source_manifest")
    if not isinstance(descriptor, dict):
        raise ValueError("synth source manifest is missing")
    manifest = hydrate_sidecar_ref(runtime.state_dir, descriptor).payload
    request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload
    if (manifest.get("attempt_id") != operation["active_attempt_id"]
            or descriptor.get("sha256") != request["request"].get("source_manifest_digest")):
        raise ValueError("synth source manifest differs from the immutable request")
    progress = required_read_progress(runtime.state_dir, events,
        attempt_id=operation["active_attempt_id"], operation_id=operation["operation_id"])
    if progress is None or progress <= 0:
        raise ValueError("synth timeout has no valid required-input progress")
    recovery = {"approval_ref": approval_ref,
                "approval_sha256": hashlib.sha256(approval_bytes).hexdigest(),
                "input_read_progress": progress,
                "read_progress_attempt_id": operation["active_attempt_id"]}
    if run.status == "blocked":
        by_id = {e.id: e for e in events}
        terminal = by_id.get(run.terminal_event_id)
        escalation = by_id.get(terminal.causation_id) if terminal else None
        source = by_id.get(escalation.payload.get("source_event_id")) if escalation else None
        # Reopening clears the aggregate projection; replay uses the immutable
        # terminal occurrence that bound the original stage failure.
        aggregate = next((e for e in reversed(events) if e.type == "fanout.aggregate.completed"
            and e.origin == "kernel" and e.actor == "zf-cli"
            and e.payload.get("fanout_id") == d["fanout_id"]
            and e.payload.get("synth_event_id") == failure.id
            and e.payload.get("status") == "failed"), None)
        stage_failure_type = aggregate.payload.get("failure_event") if aggregate else None
        if (terminal is None or terminal.type != "run.goal.blocked"
                or escalation is None or escalation.type != "human.escalate"
                or escalation.payload.get("failure_class") != "stage_replan_cap_exhausted"
                or source is None or source.type != stage_failure_type
                or source.origin != "kernel" or source.actor != "zf-cli"
                or source.causation_id != failure.id
                or source.payload.get("fanout_id") != d["fanout_id"]):
            raise ValueError("blocked Run is not caused by this synth timeout")
        recovery.update(source_failure_event_id=source.id,
                        stage_replan_generation_source_event_id=escalation.id)
    return recovery


def restore_timeout_run(runtime: Any, *, events: list, failure: Any, operation: dict,
                        recovery: dict, aggregate_event: Any) -> None:
    """Restore only the already validated checkpoint through canonical owners."""
    run_id = operation["workflow_run_id"]
    if operation["status"] == "suspended":
        WorkflowOperationService(state_dir=runtime.state_dir, event_log=runtime.event_log,
            event_writer=runtime.event_writer).admit_redrive(
                operation_id=operation["operation_id"], request_hash=operation["request_hash"],
                workflow_run_id=run_id, task_id=operation["task_id"],
                source_attempt_id=operation["active_attempt_id"],
                recovery_decision_event_id=aggregate_event.id, recovery_decision_owner="kernel_replay",
                reason="approved_synth_timeout_checkpoint")
    goal_update = next((e for e in events if e.type == "run.goal.updated"
        and e.payload.get("recovery_kind") == "operator_synth_checkpoint"
        and e.payload.get("recovered_failure_event_id") == failure.id), None)
    if recovery.get("source_failure_event_id") and goal_update is None:
        goal_update = runtime.event_writer.append(ZfEvent(type="run.goal.updated", actor="zf-cli",
            origin="kernel", task_id=operation["task_id"] or None,
            causation_id=failure.id, correlation_id=run_id, payload={**recovery,
                "schema_version": "run-goal.update.v1", "run_id": run_id,
                "workflow_run_id": run_id, "status": "active", "reason": "operator_synth_checkpoint"}))
    if goal_update is not None:
        from zf.core.task.store import TaskStore
        from zf.runtime.workflow_task_lifecycle import reopen_workflow_managed_task_from_run_recovery

        reopen_workflow_managed_task_from_run_recovery(
            task_store=TaskStore(runtime.state_dir / "kanban.json"),
            event_writer=runtime.event_writer, recovery_event=goal_update)
