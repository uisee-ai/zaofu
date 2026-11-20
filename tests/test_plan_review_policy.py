"""Explicit review selection reaches compiled roles, proposals and bound previews."""
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.config.plan_review import resolve_plan_review, validate_plan_review
from zf.core.config.workflow_profiles import expand_prd_flow
from zf.runtime.plan_execution_review import plan_review_briefing, planning_execution_context
from zf.runtime.workflow_controller_candidate import materialize_controller_candidate
from zf.runtime.run_contract import build_run_contract, write_run_contract_snapshot
from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
from zf.runtime.plan_review_policy_view import plan_review_policy_view


def policy(mode):
    return {"mode": mode, "reason": "Owner authorized review scope based on the requirement",
            "focus": ["Failure-state preservation across both entrypoints"] if mode == "targeted" else []}


def base(tmp_path):
    path = tmp_path / "zf.yaml"
    path.write_text('''apiVersion: zaofu.dev/v1
kind: PrdFlow
metadata: {name: review}
spec: {lanes: 2, backend: mock}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: review}
spec:
  version: '1.0'
  project: {name: review, state_dir: custom-state}
  workflow: {plan_approval: true}
''')
    return path


@pytest.mark.parametrize("mode", ["none", "targeted", "full"])
def test_review_choice_reaches_candidate_roles_run_contract_and_preview(tmp_path, mode):
    path = base(tmp_path)
    before = path.read_bytes()
    chosen = policy(mode)
    candidate = materialize_controller_candidate(base_path=path, request_id="review-1", short_spec={
        "flow_family": "PrdFlow", "parameters": {"lanes": 2, "plan_review": chosen}})
    assert candidate != path and path.read_bytes() == before
    config = load_config(candidate)
    stage = next(stage for stage in config.workflow.stages if stage.id == "prd-plan")
    assert bool(stage.aggregate.synth_role) == (mode != "none")
    assert config.workflow.plan_approval_enabled
    assert chosen["reason"] in '\n'.join(planning_execution_context(config, flow_kind="prd"))
    assert chosen["reason"] in '\n'.join(plan_review_briefing(config, flow_kind="prd"))
    state = tmp_path / "custom-state"
    contract = build_run_contract(config, config_path=candidate, project_root=tmp_path, state_dir=state)
    snapshot = write_run_contract_snapshot(state, contract)
    package = build_plan_artifact_package(workflow_run_id="run-1", flow_kind="prd",
        producer_stage_id="prd-plan", run_contract=snapshot, plan_revision="r1", task_map_generation="g1", produced=[])
    descriptor = write_plan_artifact_package(state, package)
    payload = {"workflow_run_id": "run-1", "plan_artifact_package_ref": descriptor["ref"],
               "plan_artifact_package_digest": descriptor["sha256"]}
    view = plan_review_policy_view(state, payload)
    assert view["mode"] == mode, view
    assert view["focus"] == chosen["focus"]
    assert "已通过" not in view["label"] or "不代表已通过" in view["label"]
    # Config edits cannot change the interpretation of the old Plan.
    path.write_text(path.read_text().replace("lanes: 2", "lanes: 3"))
    assert plan_review_policy_view(state, payload) == view
    assert plan_review_policy_view(state, {**payload, "workflow_run_id": "foreign"})["mode"] == "unknown"
    assert plan_review_policy_view(state, {**payload, "plan_artifact_package_digest": "0" * 64})["mode"] == "unknown"


@pytest.mark.parametrize("value", [None, {}, {"mode": "auto", "reason": "guess"},
    {"mode": "targeted", "reason": "scope absent"}, {"mode": "none", "reason": "", "focus": []},
    {"mode": "none", "reason": "none", "focus": ["claim"]},
    {"mode": "full", "reason": "full", "focus": [None]},
    {"mode": "full", "reason": "full", "author_approved": True}])
def test_malformed_or_implicit_policy_is_rejected(value):
    with pytest.raises(ValueError):
        validate_plan_review(value)


def test_conflicting_switches_and_light_route_are_rejected():
    with pytest.raises(ValueError, match="conflicts"):
        resolve_plan_review({"plan_review": policy("targeted"), "planCriticEnabled": False})
    with pytest.raises(ValueError, match="fanout"):
        expand_prd_flow({"topology": "light", "planReview": policy("full")})
    assert expand_prd_flow({})["metadata"].get("plan_review") is None
    assert plan_review_policy_view(Path('.'), {})["mode"] == "unknown"


def test_existing_targeted_choice_is_not_silently_overwritten_by_legacy_toggle(tmp_path):
    path = base(tmp_path)
    candidate = materialize_controller_candidate(base_path=path, request_id="review-1", short_spec={
        "flow_family": "PrdFlow", "parameters": {"lanes": 2, "plan_review": policy("targeted")}})
    with pytest.raises(ValueError, match="explicit plan_review"):
        materialize_controller_candidate(base_path=candidate, request_id="review-2", short_spec={
            "flow_family": "PrdFlow", "parameters": {"lanes": 2, "plan_critic_enabled": False}})


@pytest.mark.parametrize("mode,expected", [("full", "full"), ("targeted", "incremental")])
def test_selected_mode_controls_actual_critic_dispatch_reads(tmp_path, mode, expected):
    from tests.test_call_result_runtime import _runtime
    from tests.test_plan_local_review import _policy_fixture
    from zf.runtime.call_result_runtime import prepare_call_operation

    runtime = _runtime(tmp_path)
    runtime.config.workflow.flow_metadata["plan_review"] = policy(mode)
    manifest, _, events = _policy_fixture(runtime.state_dir)
    for event in events:
        runtime.event_writer.append(event)
    contract = next(s for s in manifest["sources"] if s["source_id"] == "plan-synth-contract")
    payload = {"workflow_run_id": "run-1", "fanout_id": "new-fanout", "stage_id": "plan",
        "role_instance": "critic", "output_profile_id": "plan-synth", "input_refs": manifest["sources"],
        "canonical_success_event": "fanout.synth.completed", "canonical_failure_event": "fanout.synth.completed",
        "plan_revision": "new-rev", "plan_synth_contract_ref": contract["ref"],
        "plan_synth_contract_digest": contract["sha256"], "reviewed_plan_candidate_digest": contract["sha256"]}
    prepared = prepare_call_operation(runtime, payload=payload, operation_type="fanout_synth",
        operation_key="critic", stage_id="plan", task_id="", dispatch_id="new-attempt", causation_id="trigger")
    assert prepared.should_dispatch
    assert payload["plan_review_policy"] == policy(mode)
    assert payload["plan_review_optimization"]["mode"] == expected


def test_synthesis_and_bound_proposal_accept_targeted_policy(tmp_path):
    from tests.test_workflow_synthesis import _context, _result
    from zf.runtime.workflow_synthesis import run_workflow_synthesis
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    config, state, writer, projection = _context(tmp_path, kind="prd")
    result = _result(projection, "PrdFlow")
    result["short_flow_spec"]["parameters"]["plan_review"] = policy("targeted")
    outcome = run_workflow_synthesis(state_dir=state, project_root=tmp_path, config=config,
        writer=writer, request_id="req-synth", actor="test", candidate_result=result)
    spec = hydrate_sidecar_ref(state, outcome.result["short_flow_spec_ref"]).payload
    assert spec["parameters"]["plan_review"] == policy("targeted")


def test_legacy_enable_cannot_leave_existing_none_policy_silently_unchanged(tmp_path):
    path = base(tmp_path)
    candidate = materialize_controller_candidate(base_path=path, request_id="review-1", short_spec={
        "flow_family": "PrdFlow", "parameters": {"lanes": 2, "plan_review": policy("none")}})
    with pytest.raises(ValueError, match="explicit plan_review"):
        materialize_controller_candidate(base_path=candidate, request_id="review-2", short_spec={
            "flow_family": "PrdFlow", "parameters": {"lanes": 2, "plan_critic_enabled": True}})


def test_policy_browser_fixture_uses_real_operator_preview_projection(tmp_path):
    from zf.core.events import EventLog, EventWriter
    from tests.plan_review_policy_fixture import add_plan_review_policy_fixtures
    from zf.runtime.operator_plan_preview import build_plan_preview

    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts/full-plan.md").write_text("# Product Plan\n\nKeep failure state.")
    add_plan_review_policy_fixtures(tmp_path, writer)
    for mode in ("none", "targeted", "full"):
        view = build_plan_preview(tmp_path, writer.event_log.read_all(), plan_id=f"evt-policy-{mode}")
        assert view["critic_review"]["mode"] == mode
        assert view["status"] == "pending"
        assert "Product Plan" in view["markdown"]


def test_compilation_cannot_silently_replace_selected_review_policy():
    from zf.runtime.workflow_proposal import _synthesis_compatibility_blockers

    selected = policy("targeted")
    args = dict(synthesis={"selected_flow_family": "PrdFlow"},
        short_spec={"flow_family": "PrdFlow", "parameters": {"plan_review": selected}},
        source_docs=[{"kind": "PrdFlow", "spec": {"planReview": policy("none")}}],
        preflight={}, closure={}, completion_profile={})
    blockers = _synthesis_compatibility_blockers(**args)
    assert len(blockers) == 1
    assert blockers[0]["kind"] == "workflow_synthesis_compile_mismatch"
    assert "plan_review" in blockers[0]["message"]
    args["source_docs"][0]["spec"]["planReview"] = selected
    assert _synthesis_compatibility_blockers(**args) == []
