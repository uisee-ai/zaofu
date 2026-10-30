from pathlib import Path

import yaml

from zf.core.config.loader import load_config


def test_versioned_prd_v5_production_config_loads_the_blocking_pipeline(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_TASK_PIPELINE_MODE", "shadow")
    source = Path(__file__).parents[1] / "zf-prd-v5.yaml"
    config = load_config(source)
    documents = list(yaml.safe_load_all(source.read_text(encoding="utf-8")))

    assert config.version == "5.0.1"
    assert documents[0]["metadata"]["name"] == "zaofu-prd/v5-production"
    assert documents[1]["metadata"]["name"] == "zaofu-prd-v5-production"
    assert documents[1]["metadata"]["zaofu"]["catalog"]["preferred"] is True
    assert documents[0]["spec"]["backend"] == "codex"
    assert documents[0]["spec"]["lanes"] == 8
    assert documents[0]["spec"]["roleDefaults"]["model"] == "${ZF_CODEX_MODEL:-gpt-6.1-sol}"
    assert documents[0]["spec"]["roleDefaults"]["model_reasoning_effort"] == "medium"
    assert documents[1]["metadata"]["zaofu"]["catalog"]["backend"] == "codex"
    assert config.project.name == "zaofu"
    assert config.workflow.harness_profile == "strict"
    assert config.workflow.orchestration.oa_profile == "orchestrator"
    assert config.workflow.orchestration.normal_frontier_owner == "wrc"
    assert config.orchestrator.backend == "codex"
    assert config.runtime.run_manager.backend == "codex"
    assert {role.backend for role in config.roles} == {"codex"}
    assert {
        next(role for role in config.roles if role.instance_id == instance).model_reasoning_effort
        for instance in {"product-scan", "tech-scan", "planner", "plan-critic", "dev-lane-0", "verify-lane-0"}
    } == {"medium"}
    assert {
        role.name for role in config.roles
        if role.name in {
            "research_root",
            "source_researcher",
            "product_analyst",
            "technical_analyst",
            "risk_critic",
            "synthesizer",
        }
    } == {
        "research_root",
        "source_researcher",
        "product_analyst",
        "technical_analyst",
        "risk_critic",
        "synthesizer",
    }
    assert config.runtime.run_manager.resident_agent.enabled is True
    assert config.runtime.run_manager.resident_agent.session_mode == "dedicated"
    assert config.runtime.workdirs.mode == "worktree"

    task_pipeline = config.workflow.flow_metadata["task_pipeline"]
    assert task_pipeline["profile_id"] == "prd-flow-v4-task-pipeline"
    assert task_pipeline["mode"] == "blocking"
    assert task_pipeline["max_active_task_pipelines"] == 16
    assert task_pipeline["pools"]["impl"]["capacity"] == 8
    assert task_pipeline["pools"]["verify"]["capacity"] == 8
    assert task_pipeline["backpressure"]["max_unverified_tasks"] == 8
    assert task_pipeline["worker_lifecycle"]["mode"] == "on_demand"
    assert task_pipeline["candidate"]["final_verify_target"] == "frozen_exact_commit"

    prd_policy = config.workflow.orchestration.flow_policies["prd"]
    assert prd_policy.checkpoint_policies["pre_impl"] == "shadow"
    assert prd_policy.checkpoint_policies["semantic_failure"] == "blocking"
    assert prd_policy.shadow_sample_percent == 100

    orchestrator = next(
        role for role in config.roles if role.name == "orchestrator"
    )
    assert "orchestrator.semantic.checkpoint.requested" in orchestrator.triggers
    assert orchestrator.backend == "codex"
    assert orchestrator.execution.default_profile == "bounded-direct-v1"
    assert config.workflow.execution_profiles["bounded-direct-v1"].limits.timeout_seconds == 600
    assert config.workflow.execution_profiles["bounded-direct-v1"].limits.token_budget == 1500000
    assert task_pipeline["worker_lifecycle"]["idle_seconds"] == 600
    for role in config.roles:
        if role.flow_kind != "prd" and role.instance_id != "orchestrator":
            continue  # Collaboration/research routes retain their separate policies.
        assert role.stuck_threshold_seconds == 600, role.instance_id
        if role.lifecycle.mode == "on_demand":
            assert role.lifecycle.idle_seconds == 600, role.instance_id
    assert orchestrator.lifecycle.mode == "resident"


def test_v5_preview_resolves_profile_sources_and_inherited_skills():
    from zf.runtime.workflow_review_fields import flow_input_roles, config_review_fields
    source = Path(__file__).parents[1] / "zf-prd-v5.yaml"
    documents = list(yaml.safe_load_all(source.read_text()))
    roles = flow_input_roles(documents, 0, "prd", config_path=source)
    assert "zf-prd-plan-synth" in roles["planner"]
    assert "zf-yoke-dev-worker-role-context" in roles["impl"]
    fields = config_review_fields(0, documents[0]["spec"], role_names=list(roles), family="prd", inherited_skills=roles)
    assert fields["config.0.taskPipeline"]["value"]["mode"] == "blocking"
    assert fields["config.0.roleSkillBundles.planner"]["value"] == roles["planner"]
