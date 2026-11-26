from copy import deepcopy

import pytest

from tests import test_product_acceptance as product
from tests.test_candidate_reader_assignments import _assignments
from tests.test_plan_candidate_preflight import _source_index, _task_map
from tests.test_workflow_registered_controller import _candidate, assembly
from zf.core.config.loader import load_config
from zf.core.config.stage_teams import normalize_stage_teams
from zf.runtime.candidate_reader_assignments import bind_reader_obligations
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight


def _team(roles=("reader-a", "reader-b")):
    return {"width": 2, "reason": "Review module behavior and the browser journey independently.",
            "members": [{"obligation_id": f"check-{i}", "role": role, "question": f"Verify evidence for {ref}",
                         "input_refs": [ref], "expected_output": f"Frozen-target evidence for {ref}"}
                        for i, (role, ref) in enumerate(zip(roles, ("journey:journey-create", "acceptance:TASK-1/AC-2")))]}


def test_registered_controller_binds_candidate_members_to_planner_brief(assembly):
    team = _team(("verify-lane-1", "verify-lane-2"))
    assembly[3]["requested_roles"].extend([m["role"] for m in team["members"]])
    assembly[3]["short_flow_spec"]["parameters"]["stage_teams"] = {"candidate_verify": team}
    _, _, path = _candidate(assembly)
    cfg = load_config(path)
    assert cfg.workflow.flow_metadata["stage_teams"]["candidate_verify"] == team
    plan = next(s for s in cfg.workflow.stages if s.id == "prd-plan")
    for member in team["members"]:
        assert any(member["role"] in line and member["input_refs"][0] in line for line in plan.criteria.instructions)


@pytest.mark.parametrize("fault", ["partial", "duplicate_role", "duplicate_evidence", "invalid_ref"])
def test_proposal_rejects_ambiguous_evidence_owners(fault):
    team = _team()
    left, right = team["members"]
    if fault == "partial":
        team["members"].pop()
    elif fault == "duplicate_role":
        right["role"] = left["role"]
    elif fault == "duplicate_evidence":
        right["input_refs"] = left["input_refs"]
    else:
        right["input_refs"] = ["arbitrary/path"]
    with pytest.raises(ValueError):
        normalize_stage_teams({"candidate_verify": team})


def test_planner_submit_reports_wrong_evidence_owner_before_review(tmp_path):
    spec = product._spec()
    spec["candidate_reader_assignments"] = rows = _assignments()
    rows[1]["journey_ids"] = rows[0]["journey_ids"]
    rows[0]["journey_ids"] = []
    result = evaluate_plan_candidate_preflight(state_dir=tmp_path / ".zf", project_root=tmp_path,
        reports=[{"report": {"task_map": _task_map(), "source_index": _source_index(),
                             "source_index_ref": "inline:source-index", "plan_md": "# Plan",
                             "plan_ports": [{"logical_name": "product_acceptance_spec", "body": spec}]}}],
        manifest={"fanout_id": "fan-1", "stage_id": "prd-plan", "workflow_run_id": "run-1",
                  "trigger_payload": {"flow_kind": "prd"}},
        metadata={"flow_kind": "prd", "stage_teams": {"candidate_verify": _team()}})
    assert any(row["code"] == "product_acceptance_spec_invalid" and "journey:journey-create" in row["message"]
               and "reader-a" in row["message"] for row in result["errors"])


def test_frozen_obligation_cannot_be_moved_duplicated_or_replayed_under_changed_authority():
    spec = product._spec()
    spec["candidate_reader_assignments"] = rows = _assignments()
    team = _team()
    bound = bind_reader_obligations(spec, team)
    assert "stage_obligation" not in rows[0]
    assert bind_reader_obligations(bound, team, frozen=True) == bound
    changed = deepcopy(team)
    changed["members"][0]["expected_output"] = "Different deliverable"
    with pytest.raises(ValueError, match="differs from the approved"):
        bind_reader_obligations(bound, changed, frozen=True)
    with pytest.raises(ValueError, match="approved evidence owner"):
        bind_reader_obligations(bound, {})
    rows[1]["journey_ids"] = ["journey-create"]
    with pytest.raises(ValueError, match="exclusively"):
        bind_reader_obligations(spec, team)


def test_actual_fanout_dispatch_exposes_only_the_signed_evidence_obligation(tmp_path, monkeypatch):
    from tests.test_candidate_reader_assignments import _scoped_project
    from tests.test_reader_fanout_runtime import _config, _RecordingTransport
    from zf.core.config.schema import RoleConfig
    from zf.runtime.orchestrator import Orchestrator

    team = _team()
    seeded = _scoped_project(tmp_path, monkeypatch, team=team)
    cfg = _config()
    cfg.roles = [RoleConfig(name=r, instance_id=r, backend="mock", role_kind="reader")
                 for r in ("reader-a", "reader-b", "discovery")]
    cfg.workflow.stages = seeded.config.workflow.stages
    cfg.workflow.flow_metadata = seeded.config.workflow.flow_metadata
    gate = cfg.workflow.stages[0].aggregate
    gate.child_success_event = "test.child.passed"
    gate.child_failure_event = "test.child.failed"
    gate.failure_event = "test.failed"
    transport = _RecordingTransport()
    runtime = Orchestrator(seeded.state_dir, cfg, transport)
    runtime.run_once(events=[seeded.event_log.read_all()[-1]])
    briefs = {role: path.read_text() for role, path, *_ in transport.sent}
    assert set(briefs) == {"reader-a", "reader-b"}
    for member in team["members"]:
        assert member["question"] in briefs[member["role"]]
        assert member["expected_output"] in briefs[member["role"]]
        assert f"Assigned evidence: {member['input_refs'][0]}" in briefs[member["role"]]
        assert all(member["question"] not in text for role, text in briefs.items() if role != member["role"])


def test_plan_package_binds_approved_obligations_into_immutable_reader_assignments(tmp_path):
    import json
    from zf.runtime.plan_artifact_package import prepare_plan_artifact_package, PlanArtifactPackageError
    from zf.runtime.run_contract import stable_json_sha256, write_run_contract
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    state = tmp_path / ".zf"
    artifacts = state / "artifacts"
    artifacts.mkdir(parents=True)
    contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    write_run_contract(state, {**contract, "contract_digest": stable_json_sha256(contract)})
    (artifacts / "req.md").write_text("Deliver both checks.")
    (artifacts / "map.json").write_text(json.dumps({"schema_version": "task-map.v1",
        "tasks": [{"task_id": "TASK-1", "acceptance_criteria": [{"id": "AC-1"}, {"id": "AC-2"}]}]}))
    team = _team()
    spec = product._spec()
    spec["candidate_reader_assignments"] = _assignments()
    args = dict(state_dir=state, project_root=tmp_path, events=[], workflow_run_id=product.RUN_ID,
                flow_kind="prd", producer_stage_id="prd-plan", goal_id="GOAL",
                metadata={"artifact_package": {"mode": "blocking"}, "stage_teams": {"candidate_verify": team},
                          "task_pipeline": {"pools": {"verify": {"role_instances": ["reader-a", "reader-b"]}}}},
                payload={"task_map_ref": ".zf/artifacts/map.json", "prd_ref": ".zf/artifacts/req.md",
                         "plan_ports": [{"logical_name": "product_acceptance_spec", "body": spec}]})
    package, _, _ = prepare_plan_artifact_package(**args)
    port = next(p for p in package["produced"] if p["logical_name"] == "product_acceptance_spec")
    rows = hydrate_sidecar_ref(state, port).payload["candidate_reader_assignments"]
    assert [r["stage_obligation"] for r in rows] == team["members"]
    spec["candidate_reader_assignments"][0]["stage_obligation"] = team["members"][1]
    with pytest.raises(PlanArtifactPackageError, match="differs from the approved owner"):
        prepare_plan_artifact_package(**args)
