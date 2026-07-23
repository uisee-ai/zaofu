"""Controller Flow smoke matrix for short product YAMLs."""

from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.config.render import build_config_inspection_report


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_DIR = ROOT / "examples" / "prod" / "controller"


def _inspect(name: str) -> dict:
    config_path = CONTROLLER_DIR / name
    config = load_config(config_path)
    return build_config_inspection_report(
        config,
        config_path=config_path,
        project_root=config_path.parent,
        state_dir=config_path.parent / config.project.state_dir,
    )


def _config(name: str):
    return load_config(CONTROLLER_DIR / name)


def _policy_by_field(report: dict) -> dict[str, dict]:
    return {
        item["field"]: item
        for item in report["diagnostics"]
        if item["kind"] in {"flow_policy_consumer", "flow_policy_without_consumer"}
    }


def _generated_pipeline(report: dict) -> dict:
    pipelines = report["generated"]["pipelines"]
    assert len(pipelines) == 1
    return pipelines[0]


def _assert_profile_sources(report: dict) -> None:
    profiles = report["source"]["profiles"]
    assert profiles
    assert all(item.get("sha256") for item in profiles)


def _assert_flow_kernel_contract(
    name: str,
    *,
    final_inside_pipeline: bool = True,
) -> None:
    config = _config(name)
    pipelines = list(config.workflow.pipelines)
    assert len(pipelines) == 1
    pipeline = pipelines[0]
    assert pipeline.trigger == "task_map.ready"
    assert pipeline.affinity_key == "affinity_tag"
    assert pipeline.overflow == "first_released_lane"
    assert pipeline.stage_transition == "stage_barrier"
    assert pipeline.final_barrier == ""
    assert [stage.stage_id for stage in pipeline.stages] == ["impl", "verify"]
    impl = pipeline.stages[0]
    assert impl.success_event == "dev.build.done"
    assert impl.failure_event == "dev.failed"
    verify = pipeline.stages[1]
    assert verify.success_event == "verify.child.completed"
    assert verify.failure_event == "verify.child.failed"
    assert verify.rework_to == "impl"
    assert verify.feedback_artifact == "required"
    if final_inside_pipeline:
        assert pipeline.final_when == "all_tasks_verified"
        assert pipeline.final_success == "goal.closure.synthesized"
        assert pipeline.final_failure == "goal.closure.synthesis.failed"
    else:
        assert pipeline.final_when == ""
        assert pipeline.final_success == ""
        assert pipeline.final_failure == ""
    stages = {stage.id: stage for stage in config.workflow.stages}
    impl_stage = next(
        stage for stage in stages.values()
        if stage.topology == "fanout_writer_scoped"
        and str(stage.id).endswith("-impl")
    )
    assert impl_stage.aggregate.child_success_event == "dev.build.done"
    assert impl_stage.aggregate.child_failure_event == "dev.failed"


def _assert_discovery_stage(name: str, stage_id: str) -> None:
    config = _config(name)
    stages = {stage.id: stage for stage in config.workflow.stages}
    assert stage_id in stages
    stage = stages[stage_id]
    assert stage.trigger == "flow.discovery.requested"
    assert stage.topology == "fanout_reader"
    assert stage.roles == ["flow-discovery"]
    assert stage.aggregate.success_event == "flow.discovery.completed"
    assert stage.aggregate.failure_event == "flow.discovery.failed"


def test_issue_flow_controller_smoke_matrix() -> None:
    report = _inspect("issue-fanout-v3.yaml")

    assert report["status"] in {"GO", "WARN"}
    assert report["generated"]["flow_metadata"]["flow_kind"] == "issue"
    assert report["generated"]["flow_metadata"]["post_verify_discovery"] == "regression_impact"
    pipeline = _generated_pipeline(report)
    assert pipeline["stage_transition"] == "stage_barrier"
    _assert_profile_sources(report)
    policy = _policy_by_field(report)
    assert policy["quality_floor"]["detail"]["value"] == "issue-regression"
    assert policy["quality_floor"]["detail"]["enforcement_status"] == "planned_consumer"
    assert "final judge gate" in policy["quality_floor"]["detail"]["target_gates"]
    _assert_discovery_stage("issue-fanout-v3.yaml", "issue-post-verify-discovery")
    _assert_flow_kernel_contract("issue-fanout-v3.yaml")
    config = _config("issue-fanout-v3.yaml")
    assert config.workflow.kind_routes["issue"].pattern_id == "issue-triage"
    assert config.workflow.pipelines[0].lane_count == 1
    assert len([role for role in config.roles if role.name.startswith("fix-lane-")]) == 1
    assert len([role for role in config.roles if role.name.startswith("verify-lane-")]) == 1
    assert config.workflow.admission_replan.enabled is True
    assert config.workflow.admission_replan.resynth_trigger == "issue.requested"


def test_prd_flow_controller_smoke_matrix() -> None:
    report = _inspect("prd-fanout-v3.yaml")

    assert report["status"] in {"GO", "WARN"}
    metadata = report["generated"]["flow_metadata"]
    assert metadata["flow_kind"] == "prd"
    assert metadata["post_verify_discovery"] == "product_completeness"
    assert metadata["delivery_policy"] == "ship_candidate"
    assert _generated_pipeline(report)["stage_transition"] == "stage_barrier"
    _assert_profile_sources(report)
    policy = _policy_by_field(report)
    assert policy["quality_floor"]["detail"]["value"] == "product-demo"
    assert "terminal evidence gate" in policy["evidence_policy"]["detail"]["target_gates"]
    control_room = report["coverage"]["control_room_contract"]
    assert control_room["enabled"] is True
    assert "pending_action" in control_room["required_fields"]
    assert "flow.gap_plan.ready" in control_room["event_sources"]
    _assert_discovery_stage("prd-fanout-v3.yaml", "prd-post-verify-discovery")
    _assert_flow_kernel_contract("prd-fanout-v3.yaml")
    config = _config("prd-fanout-v3.yaml")
    assert config.workflow.kind_routes["prd"].pattern_id == "prd-scan"
    assert config.workflow.admission_replan.enabled is True
    assert config.workflow.admission_replan.resynth_trigger == "prd.scan.completed"


def test_claude_full_controllers_declare_submit_entrypoints() -> None:
    prd = _config("prd-fanout-v3-claude.yaml")
    issue = _config("issue-fanout-v3-claude.yaml")

    assert prd.workflow.kind_routes["prd"].pattern_id == "prd-scan"
    assert issue.workflow.kind_routes["issue"].pattern_id == "issue-triage"


def test_refactor_flow_controller_smoke_matrix() -> None:
    report = _inspect("refactor-lane-v3.yaml")

    assert report["status"] in {"GO", "WARN"}
    metadata = report["generated"]["flow_metadata"]
    assert metadata["flow_kind"] == "refactor"
    assert metadata["post_verify_discovery"] == "module_parity"
    assert _generated_pipeline(report)["stage_transition"] == "stage_barrier"
    _assert_profile_sources(report)
    policy = _policy_by_field(report)
    assert policy["gap_loop"]["kind"] == "flow_policy_consumer"
    assert policy["gap_loop"]["detail"]["enforcement_status"] == "wired"
    assert "gap-scoped task_map.ready" in policy["gap_loop"]["detail"]["target_gates"]
    assert policy["completion_threshold"]["detail"]["target_gates"]
    _assert_flow_kernel_contract(
        "refactor-lane-v3.yaml",
        final_inside_pipeline=False,
    )
    config = _config("refactor-lane-v3.yaml")
    final = next(
        stage for stage in config.workflow.stages
        if stage.id == "flow-final-judge"
    )
    assert final.trigger == "module.parity.closed"
