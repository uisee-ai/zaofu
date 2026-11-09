"""Input freshness from the existing Plan Package reducer, never a second owner."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.runtime.plan_artifact_package import (
    admitted_plan_artifact_package_for_generation,
    reduce_plan_artifact_packages,
)


def input_lifecycle(manifest: Mapping[str, Any], events: Sequence[Any], *,
                    state_dir: Path | None = None) -> dict[str, Any]:
    ref = str(manifest.get("plan_artifact_package_ref") or "")
    digest = str(manifest.get("plan_artifact_package_digest") or "")
    relevant = [event for event in events if event.type in {
        "plan.artifact_package.admitted", "plan.artifact_package.rejected", "plan.artifact_package.superseded", "fanout.cancelled"
    } and event.payload.get("workflow_run_id") == manifest.get("workflow_run_id")]
    result = {"status": "pinned", "authorizes_rebind": False, "read_binding": "immutable_attempt",
              "source_watermark": str(relevant[-1].id) if relevant else "", "requires_rebind": False}
    if not ref or not digest:
        return {**result, "reason": "no_plan_package_binding"}
    try:
        reduced = reduce_plan_artifact_packages(events, workflow_run_id=str(manifest.get("workflow_run_id") or ""))
    except ValueError:
        return {**result, "status": "unknown", "reason": "package_projection_conflict", "requires_rebind": True}
    def matches(row):
        return row.get("package_ref") == ref and (row.get("package_digest") or row.get("package_sha256")) == digest
    current = reduced.get("current") or {}
    if matches(current):
        return {**result, "status": "current"}
    if any(matches(row) for row in reduced.get("history", [])):
        try:
            pinned = admitted_plan_artifact_package_for_generation(
                reduced, task_map_generation=str(manifest.get("task_map_generation") or ""))
            if matches(pinned) and state_dir is not None:
                if _current_task_binding(state_dir, manifest, events):
                    return {**result, "reason": "current_task_generation_binding"}
                if _current_recovery_reader_binding(state_dir, manifest, events):
                    return {**result, "reason": "current_recovery_reader_binding"}
        except (OSError, ValueError):
            pass  # Unprovable or conflicting bindings remain closed.
        return {**result, "status": "superseded", "requires_rebind": True}
    if any(matches(row) for row in reduced.get("rejected", [])):
        return {**result, "status": "rejected", "requires_rebind": True}
    return {**result, "status": "unadmitted", "requires_rebind": True}


def _current_task_binding(state_dir: Path, manifest: Mapping[str, Any], events: Sequence[Any]) -> bool:
    """A package update is not revocation of another Task's running attempt."""
    from zf.core.task.store import TaskStore
    from zf.runtime.task_contract_snapshot import current_task_contract_identity
    from zf.runtime.workflow_operation import reduce_workflow_operations

    authority = manifest.get("handoff_authority_contract") or {}
    if not isinstance(authority, Mapping) or authority.get("attempt_domain") != "task":
        return False
    task = TaskStore(state_dir / "kanban.json").get(str(manifest.get("task_id") or ""))
    if (task is None or task.status != "in_progress" or not task.active_dispatch_id
            or task.active_dispatch_id != manifest.get("dispatch_id")):
        return False
    identity = current_task_contract_identity(task)
    if any(not manifest.get(key) or identity.get(key) != manifest[key] for key in (
        "task_id", "workflow_run_id", "contract_revision", "task_map_generation",
    )):
        return False
    operations = reduce_workflow_operations(events).values()
    matching = [row for row in operations if row.get("active_attempt_id") == manifest.get("attempt_id")]
    if len(matching) != 1:
        return False
    row = matching[0]
    return (row.get("status") == "running" and row.get("role_instance") == task.assigned_to
            and all(row.get(key) == manifest[key] for key in (
                "task_id", "workflow_run_id", "dispatch_id", "task_map_generation")))


def _current_recovery_reader_binding(state_dir: Path, manifest: Mapping[str, Any], events: Sequence[Any]) -> bool:
    """A failed Task's reader uses its own live operation, not the retired Impl dispatch.

    This permits reading an unchanged admitted generation; it grants no execution,
    rebind, result admission or task-state authority.
    """
    from zf.core.task.store import TaskStore
    from zf.runtime.call_result_envelope import canonical_json_sha256
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.task_contract_snapshot import current_task_contract_identity
    from zf.runtime.workflow_operation import (
        TERMINAL_OPERATION_STATUSES, operation_request_hash, reduce_workflow_operations,
    )

    authority = manifest.get("handoff_authority_contract") or {}
    if (not isinstance(authority, Mapping) or authority.get("attempt_domain") != "task"
            or authority.get("operation_type") != "fanout_reader_child" or not authority.get("continuation_key")):
        return False
    task = TaskStore(state_dir / "kanban.json").get(str(manifest.get("task_id") or ""))
    if task is None or task.status not in {"in_progress", "failed", "blocked"}:
        return False
    identity = current_task_contract_identity(task)
    keys = ("task_id", "workflow_run_id", "contract_revision", "task_map_generation")
    if any(not manifest.get(key) or identity.get(key) != manifest[key] for key in keys):
        return False
    failure = next((e for e in events if e.id == authority["continuation_key"]), None)
    if (failure is None or (failure.task_id or failure.payload.get("task_id")) != task.id
            or any(failure.payload.get(key) != manifest[key] for key in keys if key != "task_id")):
        return False
    operations = list(reduce_workflow_operations(events).values())
    if any(row.get("task_id") == task.id and row.get("workflow_run_id") == manifest["workflow_run_id"]
           and row.get("task_pipeline_stage") in {"impl", "verify"}
           and row.get("status") not in TERMINAL_OPERATION_STATUSES for row in operations):
        return False
    matching = [row for row in operations
                if row.get("active_attempt_id") == manifest.get("attempt_id")]
    if len(matching) != 1:
        return False
    row = matching[0]
    if (row.get("status") != "running" or row.get("operation_type") != "fanout_reader_child"
            or row.get("attempt_domain") != "task" or not row.get("lease_id")
            or any(not manifest.get(key) or row.get(key) != manifest[key] for key in (
                "task_id", "workflow_run_id", "dispatch_id", "task_map_generation"))):
        return False
    # The immutable request binds both the complete manifest and its result identity.
    body = hydrate_sidecar_ref(state_dir, row.get("request_ref") or {}).payload
    if not isinstance(body, Mapping) or operation_request_hash(body) != row.get("request_hash"):
        return False
    request = body.get("request") or {}
    if not isinstance(request, Mapping):
        return False
    bound = request.get("result_identity") or {}
    if not isinstance(bound, Mapping):
        return False
    if (request.get("source_manifest_digest") != canonical_json_sha256(manifest)
            or request.get("handoff_authority_contract") != authority
            or any(body.get(key) != row.get(key) for key in (
                "task_id", "workflow_run_id", "operation_id", "active_attempt_id", "role_instance", "lease_id"))
            or any(not manifest.get(key) or bound.get(key) != manifest[key] for key in (
                *keys, "target_commit", "plan_artifact_package_ref", "plan_artifact_package_digest"))):
        return False
    fanout = request.get("fanout_id")
    return bool(fanout) and not any(e.type == "fanout.cancelled"
        and e.payload.get("workflow_run_id") == manifest["workflow_run_id"]
        and e.payload.get("fanout_id") == fanout for e in events)
