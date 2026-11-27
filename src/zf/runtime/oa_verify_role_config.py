"""Narrow the ordinary Verify delegation's role roster without dropping shared policy."""
from __future__ import annotations

from typing import Any, Mapping

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.oa_task_verify_assignment import verify_seats
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


SOURCE = "verify-role-config"


def pin_verify_role_config(
    runtime: Any, workflow_run_id: str, payload: Mapping[str, Any], effective_ref: Mapping[str, Any],
) -> dict[str, Any]:
    # Only the ordinary role selection from the controller's newly rendered config is covered.
    if (payload.get("effective_config_ref") or payload.get("verify_specialist_patterns") != {}
            or payload.get("verify_specialist_incident") or payload.get("initial_verify_scope_ref")
            or payload.get("candidate_reader_assignments_digest") or not payload.get("verify_assignment_contract_ref")):
        return {}
    task = payload.get("verify_assignment_task")
    seats = payload.get("verify_assignment_seats")
    if (not task or not isinstance(seats, list) or not seats
            or not all(isinstance(seat, str) and seat for seat in seats)
            or sorted(verify_seats(runtime)) != sorted(seats)):
        return {}
    contract = hydrate_sidecar_ref(runtime.state_dir, payload["verify_assignment_contract_ref"]).payload
    binding = {"schema_version": "oa-verify-contract-input.v1", "workflow_run_id": workflow_run_id,
               "task_map_generation": payload.get("task_map_generation"), "task_id": task,
               "contract_revision": payload.get("verify_assignment_revision")}
    if not isinstance(contract, Mapping) or not all(value and contract.get(key) == value for key, value in binding.items()):
        return {}
    snapshot = hydrate_sidecar_ref(runtime.state_dir, effective_ref).payload
    if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != "effective-config-snapshot.v1":
        return {}
    config = snapshot.get("config")
    roles = config.get("roles") if isinstance(config, Mapping) else None
    if not isinstance(roles, list) or not all(isinstance(role, dict) and role.get("instance_id") for role in roles):
        return {}
    selected = [role for role in roles if role["instance_id"] in seats]
    # Names are accepted aliases during delegation. If a seat is ambiguous keep the full roster.
    if (len(selected) == len(roles) or {role["instance_id"] for role in selected} != set(seats)
            or any(role.get("role_kind") != "reader" for role in selected)
            or any(sum(seat in {role["instance_id"], role.get("name")} for role in roles) != 1 for seat in seats)):
        return {}
    return write_immutable_json_sidecar(
        runtime.state_dir, {
            "schema_version": "oa-verify-role-config.v1",
            "effective_config_ref": {"ref": effective_ref["ref"], "sha256": effective_ref["sha256"]},
            "eligible_reader_seats": sorted(seats),
            "config": {**config, "roles": selected},
        },
        root="orchestrator-agent/verify-role-configs", kind="oa_verify_role_config",
        schema_version="oa-verify-role-config.v1", created_by="orchestrator-agent-context",
    )
