"""Incremental plans retain the admitted requirement's mandatory claims."""

import json

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError,
    package_event_payload,
    prepare_plan_artifact_package,
)
from zf.runtime.run_contract import stable_json_sha256, write_run_contract


@pytest.mark.parametrize("replace_requirement", [False, True])
def test_gap_package_inherits_requirement_without_waiving_claims(tmp_path, replace_requirement):
    root = tmp_path / "project"
    state = root / ".state"
    artifacts = state / "artifacts"
    artifacts.mkdir(parents=True)
    requirement = {"acceptance": [{"id": "OWNER-AC", "text": "Keep the complete product scope."}]}
    (artifacts / "requirement.json").write_text(json.dumps(requirement))
    task_map = {
        "schema_version": "task-map.v1",
        "goal_claims": [{"id": "PLAN-AC", "text": "Deliver verified tasks."}],
        "tasks": [{"task_id": "T1", "acceptance_criteria": [{"id": "AC1", "text": "Works."}]}],
    }
    for revision in (1, 2):
        (artifacts / f"tasks-r{revision}.json").write_text(json.dumps(task_map))
    contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    write_run_contract(state, {**contract, "contract_digest": stable_json_sha256(contract)})
    args = dict(
        state_dir=state, project_root=root, workflow_run_id="run-gap",
        flow_kind="prd", producer_stage_id="prd-plan", goal_id="goal-gap",
        metadata={"artifact_package": {"mode": "blocking"}},
    )
    first, descriptor, _ = prepare_plan_artifact_package(
        **args, events=[], payload={
            "task_map_ref": ".state/artifacts/tasks-r1.json",
            "requirement_spec_ref": ".state/artifacts/requirement.json",
        },
    )
    admitted = ZfEvent(type="plan.artifact_package.admitted", payload=package_event_payload(
        first, descriptor, status="admitted",
    ))
    payload = {"task_map_ref": ".state/artifacts/tasks-r2.json", "resume_scope": "gap_tasks_only"}
    if replace_requirement:
        (artifacts / "replacement.json").write_text(json.dumps({"acceptance": []}))
        payload["requirement_spec_ref"] = ".state/artifacts/replacement.json"
        with pytest.raises(PlanArtifactPackageError, match="mandatory goal claim continuity failed: OWNER-AC"):
            prepare_plan_artifact_package(**args, events=[admitted], payload=payload)
        return

    second, _, claims = prepare_plan_artifact_package(**args, events=[admitted], payload=payload)
    assert claims["goal_claim_continuity"]["status"] == "passed"
    assert claims["goal_claim_continuity"]["previous_mandatory_count"] == 2
    assert any(port["logical_name"] == "requirement_spec" for port in second["inherited"])
