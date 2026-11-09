"""Validate the immutable evidence anchoring a dynamic continuation."""

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages
from zf.runtime.workflow_operation import TERMINAL_OPERATION_STATUSES, reduce_workflow_operations


def create_checkpoint(state_dir: Path, events: list, *, workflow_run_id: str,
                      parent_operation_id: str, task_id: str, task_map_generation: str) -> dict:
    """Create an immutable checkpoint only for a live, current parent scope."""
    package = reduce_plan_artifact_packages(events, workflow_run_id=workflow_run_id).get("current") or {}
    parent = reduce_workflow_operations(events).get(parent_operation_id) or {}
    allowed = {str(parent.get("task_id") or "")} | {str(v) for v in parent.get("child_task_ids") or []}
    if (not workflow_run_id or not task_id or not task_map_generation
            or package.get("task_map_generation") != task_map_generation
            or parent.get("workflow_run_id") != workflow_run_id
            or parent.get("status") in TERMINAL_OPERATION_STATUSES
            or task_id not in allowed or not parent.get("request_hash")):
        raise ValueError("checkpoint parent, task, run or generation is not current")
    return write_immutable_json_sidecar(state_dir, {
        "schema_version": "dynamic-checkpoint.v1", "workflow_run_id": workflow_run_id,
        "parent_operation_id": parent_operation_id, "parent_request_hash": parent["request_hash"],
        "task_id": task_id, "task_map_generation": task_map_generation,
    }, root="checkpoints", kind="dynamic_checkpoint", schema_version="dynamic-checkpoint.v1",
       created_by="kernel")


def checkpoint_error(state_dir: Path, action: Mapping[str, Any], parent: Mapping[str, Any]) -> str:
    try:
        checkpoint = hydrate_sidecar_ref(state_dir, {
            "ref": str(action.get("trigger_checkpoint_ref") or ""),
            "sha256": str(action.get("trigger_checkpoint_digest") or ""),
        }).payload
    except (SidecarRefError, OSError, ValueError):
        return "trigger_checkpoint_invalid"
    if not isinstance(checkpoint, Mapping) or checkpoint.get("schema_version") != "dynamic-checkpoint.v1":
        return "trigger_checkpoint_invalid"
    for key in ("workflow_run_id", "parent_operation_id", "task_id", "task_map_generation"):
        if not action.get(key) or checkpoint.get(key) != action.get(key):
            return "trigger_checkpoint_identity_mismatch"
    if not parent.get("request_hash") or checkpoint.get("parent_request_hash") != parent["request_hash"]:
        return "trigger_checkpoint_identity_mismatch"
    return ""
