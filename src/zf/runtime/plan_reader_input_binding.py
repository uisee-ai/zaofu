"""Prove a live Plan reader's immutable baseline, without granting execution."""
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash, reduce_workflow_operations


def current_plan_reader_baseline(
    state_dir: Path, manifest: Mapping[str, Any], events: Sequence[Any],
) -> bool:
    """Caller has already proved the exact baseline is historically admitted.

    Match CanonicalHandoffResolver's Plan-input semantics using the registered
    operation, not a claimed historical package or another Task's dispatch.
    """
    authority = manifest.get("handoff_authority_contract") or {}
    if (not isinstance(authority, Mapping) or authority.get("attempt_domain") != "plan"
            or authority.get("operation_type") != "fanout_reader_child"):
        return False
    operations = reduce_workflow_operations(events).values()
    matching = [row for row in operations
                if row.get("active_attempt_id") == manifest.get("attempt_id")]
    if len(matching) != 1:
        return False
    row = matching[0]
    if (row.get("status") != "running" or row.get("operation_type") != "fanout_reader_child"
            or row.get("attempt_domain") != "plan" or not row.get("lease_id")
            or any(not manifest.get(key) or row.get(key) != manifest[key] for key in (
                "workflow_run_id", "dispatch_id", "task_map_generation"))
            or row.get("task_id") != manifest.get("task_id")):
        return False
    body = hydrate_sidecar_ref(state_dir, row.get("request_ref") or {}).payload
    if not isinstance(body, Mapping) or operation_request_hash(body) != row.get("request_hash"):
        return False
    request = body.get("request") or {}
    if not isinstance(request, Mapping):
        return False
    identity = request.get("result_identity") or {}
    if not isinstance(identity, Mapping):
        return False
    if (request.get("source_manifest_digest") != canonical_json_sha256(manifest)
            or request.get("handoff_authority_contract") != authority
            or any(body.get(key) != row.get(key) for key in (
                "task_id", "workflow_run_id", "operation_id", "active_attempt_id", "role_instance", "lease_id"))
            or any(not manifest.get(key) or identity.get(key) != manifest[key] for key in (
                "workflow_run_id", "task_map_generation", "plan_artifact_package_ref",
                "plan_artifact_package_digest"))
            or str(identity.get("task_id") or "") != str(manifest.get("task_id") or "")
            or identity.get("target_commit") != manifest.get("target_commit")):
        return False
    fanout = request.get("fanout_id")
    return bool(fanout) and not any(event.type == "fanout.cancelled"
        and event.payload.get("workflow_run_id") == manifest["workflow_run_id"]
        and event.payload.get("fanout_id") == fanout for event in events)
