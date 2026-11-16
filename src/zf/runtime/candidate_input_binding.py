"""Prove a live candidate Reader's input binding without granting execution."""
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.candidate_currentness import candidate_currentness_issues
from zf.runtime.candidate_verification_authority import _latest_frozen_candidate
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash, reduce_workflow_operations


def current_candidate_input_binding(
    state_dir: Path, manifest: Mapping[str, Any], events: Sequence[ZfEvent],
) -> bool:
    authority = manifest.get("handoff_authority_contract") or {}
    if (authority.get("attempt_domain") != "task"
            or authority.get("operation_type") != "fanout_reader_child"):
        return False
    rows = [row for row in reduce_workflow_operations(events).values()
            if row.get("active_attempt_id") == manifest.get("attempt_id")]
    if len(rows) != 1:
        return False
    row = rows[0]
    if (row.get("status") != "running" or not row.get("lease_id")
            or row.get("operation_type") != "fanout_reader_child"
            or any(not manifest.get(key) or row.get(key) != manifest[key] for key in (
                "task_id", "workflow_run_id", "dispatch_id", "task_map_generation"))):
        return False
    body = hydrate_sidecar_ref(state_dir, row.get("request_ref") or {}).payload
    if operation_request_hash(body) != row.get("request_hash"):
        return False
    request = body.get("request") or {}
    identity = request.get("result_identity") or {}
    if (request.get("output_profile_id") != "candidate-verify"
            or request.get("source_manifest_digest") != canonical_json_sha256(manifest)
            or request.get("handoff_authority_contract") != authority
            or any(body.get(key) != row.get(key) for key in (
                "task_id", "workflow_run_id", "operation_id", "active_attempt_id", "role_instance", "lease_id"))
            or any(not manifest.get(key) or identity.get(key) != manifest[key] for key in (
                "task_id", "workflow_run_id", "contract_revision", "task_map_generation",
                "target_commit", "plan_artifact_package_ref", "plan_artifact_package_digest"))):
        return False
    fanout = request.get("fanout_id")
    if not fanout or any(e.type == "fanout.cancelled"
            and e.payload.get("workflow_run_id") == manifest["workflow_run_id"]
            and e.payload.get("fanout_id") == fanout for e in events):
        return False
    if candidate_currentness_issues(state_dir, events, identity=identity,
            contract_ref=str(identity.get("contract_snapshot_ref") or ""),
            contract_digest=str(identity.get("contract_snapshot_digest") or "")):
        return False
    frozen = _latest_frozen_candidate(list(events), str(manifest["workflow_run_id"]))
    if frozen is None:
        return False
    hydrate_sidecar_ref(state_dir, frozen.payload.get("freeze_receipt_ref") or {})
    return True
