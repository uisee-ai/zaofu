"""Retire obsolete OA requests without treating provider liveness as task progress."""

from typing import Any, Mapping

from zf.runtime.orchestrator_agent_plan_binding import (
    current_checkpoint_plan_package, semantic_failure_target_task_id,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


def reconcile_obsolete_checkpoint_operations(
    service: WorkflowOperationService, *, role_instance: str = "", operation_id: str = "",
) -> None:
    """Queued obsolete plans need no turn; active calls need a bound stale result.

    Never release a running provider solely because a newer plan appeared. Its
    authenticated result must already have been rejected against that old plan.
    """
    events = service.event_log.read_all()
    for row in reduce_workflow_operations(events).values():
        if (row.get("operation_type") != "orchestrator_agent_semantic"
                or row.get("status") not in {"requested", "reserved", "running", "suspended"}
                or (role_instance and row.get("role_instance") != role_instance)
                or (operation_id and row.get("operation_id") != operation_id)):
            continue
        try:
            stored = hydrate_sidecar_ref(service.state_dir, row["request_ref"]).payload
            request = stored["request"]
            if any(stored.get(key) != row.get(key)
                   for key in ("operation_id", "workflow_run_id", "operation_type")):
                continue
            # Candidate review precedes package admission and has its own
            # candidate-key supersession protocol.
            if request.get("checkpoint") == "plan_candidate":
                continue
            target = semantic_failure_target_task_id(service.state_dir, request)
            current = current_checkpoint_plan_package(
                events, workflow_run_id=row["workflow_run_id"], task_id=target,
            )
            keys = ("id", "ref", "digest")
            if not all(request.get("plan_artifact_package_" + k)
                       and current.get("package_" + k) for k in keys):
                continue
            if all(request["plan_artifact_package_" + k] == current["package_" + k]
                   for k in keys):
                continue
        except (KeyError, OSError, ValueError, TypeError):
            continue  # Missing/corrupt evidence cannot authorize retirement.
        cause = str(current.get("admitted_event_id") or current.get("event_id") or "")
        reason = "queued_checkpoint_plan_package_superseded"
        if row["status"] != "requested":
            invalid = next((e for e in reversed(events)
                if e.type == "workflow.call.result.invalid" and e.origin in {"", "kernel"}
                and e.actor == "zf-cli"
                and e.payload.get("reason") == "stale_call_result_superseded"
                and all(e.payload.get(k) == row.get(k)
                        for k in ("operation_id", "request_hash", "workflow_run_id"))
                and _same_attempt(service, row, e.payload)), None)
            if invalid is None:
                continue
            cause, reason = invalid.id, "stale_call_result_superseded"
        service.supersede(
            operation_id=row["operation_id"], request_hash=row["request_hash"],
            workflow_run_id=row["workflow_run_id"], task_id=str(row.get("task_id") or ""),
            reservation_id=str(row.get("reservation_id") or ""), reason=reason,
            causation_id=cause, correlation_id=row["workflow_run_id"],
        )


def _same_attempt(service: WorkflowOperationService, row: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    try:
        envelope = hydrate_sidecar_ref(service.state_dir, payload["envelope_ref"]).payload
        identity = envelope["identity"]
        return bool(row.get("active_attempt_id")) and all(
            identity.get(key) == expected for key, expected in {
                "operation_id": row["operation_id"], "request_hash": row["request_hash"],
                "workflow_run_id": row["workflow_run_id"], "attempt_id": row["active_attempt_id"],
            }.items()
        )
    except (KeyError, OSError, ValueError, TypeError):
        return False
