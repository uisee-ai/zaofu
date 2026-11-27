"""Bind prospective Task Verify scope to the same contracts Writer will materialize."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.oa_task_verify_assignment import _verify_assignment_acceptance
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.writer_fanout_admission import writer_task_items
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import effective_contract_revision, normalize_acceptance_criteria
from zf.runtime.verification_commands import task_contract_verification_commands, verification_command_required_for_stage


REF = "initial_verify_scope_ref"
SOURCE = "initial-task-verify-scope"
IDENTITY = ("workflow_run_id", "task_map_generation", "plan_artifact_package_ref", "plan_artifact_package_digest")


def pin_initial_verify_scope(runtime: Any, loaded: Any, payload: Mapping, event_id: str) -> dict:
    if not getattr(loaded, "task_items", None):
        return {}
    package = hydrate_plan_artifact_package(runtime.state_dir, {
        "ref": payload["plan_artifact_package_ref"], "sha256": payload["plan_artifact_package_digest"],
    })
    if (package.get("workflow_run_id") != payload["workflow_run_id"]
            or package.get("task_map_generation") != payload["task_map_generation"]):
        raise ValueError("initial Task Verify scope requires the current Run and package generation")
    maps = [p for p in [*package["produced"], *package["inherited"]] if p["logical_name"] == "task_map"]
    if len(maps) != 1:
        return {}
    task_map = hydrate_sidecar_ref(runtime.state_dir, maps[0]).payload
    pinned = {r["task_id"]: r for r in writer_task_items(task_map)}
    # Scope corrections are justified only for the exact, normalized package rows
    # Writer will materialize. Partial/legacy inputs stay with the downstream check.
    matched = {r["task_id"] for r in loaded.task_items if r == pinned.get(r["task_id"])}
    previews = runtime._ensure_writer_tasks_canonical(loaded, preview_only=True)
    facts = {}
    for task_id, task in previews.items():
        if task_id not in matched:
            continue
        contract = task.contract
        revision = effective_contract_revision(task)
        criteria = normalize_acceptance_criteria(
            contract.acceptance_criteria or [contract.acceptance], task_id=task_id,
            contract_revision=revision, verification_tiers=contract.verification_tiers)
        facts[task_id] = {
            "contract_revision": revision,
            "task_verify_acceptance_ids": _verify_assignment_acceptance(task, revision),
            "acceptance_owners": {c["acceptance_id"]: c["verification_owner"] for c in criteria},
            "task_verify_commands": [
                {"id": c["id"], "command": c["command"], "owner": c["owner"]}
                for c in task_contract_verification_commands(contract)
                if verification_command_required_for_stage(c, verification_owner="task_verify", task_id=task_id)
            ],
        }
    body = {"schema_version": "initial-task-verify-scope.v1", **{k: payload[k] for k in IDENTITY},
            "task_map_sha256": maps[0]["sha256"], "tasks": facts, "unresolved_task_ids": sorted({item["task_id"] for item in loaded.task_items} - set(facts))}
    return write_immutable_json_sidecar(runtime.state_dir, body, root="orchestrator-agent/verify-scopes",
        kind="initial_task_verify_scope", schema_version=body["schema_version"], created_by="kernel", source_event_id=event_id)


def initial_verify_errors(state_dir: Path, request: Mapping, plan: Mapping) -> list[dict]:
    descriptor = request.get(REF)
    if not descriptor:
        return []
    try:
        facts = hydrate_sidecar_ref(state_dir, descriptor).payload
        if (not isinstance(facts, Mapping) or facts.get("schema_version") != "initial-task-verify-scope.v1"
                or any(not facts.get(k) or facts[k] != request.get(k) for k in IDENTITY)):
            raise ValueError("initial Task Verify scope does not match the approved request")
    except (ValueError, OSError) as exc:
        return [{"field": REF, "code": "invalid_task_verify_scope_reference", "message": str(exc),
                 "required_change": "Restore the exact kernel-signed scope receipt before resubmission."}]
    errors = []
    for index, unit in enumerate(plan.get("graph", {}).get("work_units", [])):
        if not {"verify", "prd-lanes-verify"}.intersection(unit.get("stage_ids") or []):
            continue
        ids = (unit.get("verification_scope") or {}).get("acceptance_ids")
        if not isinstance(ids, list) or ids == ["*"]:
            continue
        for task in unit.get("task_ids") or []:
            row = facts["tasks"].get(task)
            if row is None or set(ids) == set(row["task_verify_acceptance_ids"]):
                continue
            expected = row["task_verify_acceptance_ids"]
            owners = {i: row["acceptance_owners"].get(i, "undeclared") for i in ids if i not in expected}
            change = (f"Select the exact Task Verify IDs {expected} for this task in the same scratch."
                      if expected else "No named local ACs are in scope. Retain the local commands and evidence obligation. "
                      "The existing explicit ['*'] covers only the local criteria; request Plan revision if scope needs to change.")
            errors.append({"field": f"run_plan.graph.work_units[{index}].verification_scope.acceptance_ids",
                "code": "task_verify_scope_mismatch", "work_unit_id": unit["work_unit_id"], "task_id": task,
                "message": f"{task}: selected {ids} does not match Task Verify {expected}; other owners: {owners}.",
                "selected_acceptance_ids": ids, "task_verify_acceptance_ids": expected, "other_owners": owners,
                "task_verify_commands": row["task_verify_commands"], "required_change": change})
    return errors
