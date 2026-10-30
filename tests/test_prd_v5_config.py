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

    assert config.version == "5.0"
    assert documents[0]["metadata"]["name"] == "zaofu-prd/v5-production"
    assert documents[1]["metadata"]["name"] == "zaofu-prd-v5-production"
    assert documents[1]["metadata"]["zaofu"]["catalog"]["preferred"] is True
    assert documents[0]["spec"]["backend"] == "codex"
    assert documents[0]["spec"]["lanes"] == 8
    assert documents[1]["metadata"]["zaofu"]["catalog"]["backend"] == "codex"
    assert config.project.name == "zaofu"
    assert config.workflow.harness_profile == "strict"
    assert config.workflow.orchestration.oa_profile == "orchestrator"
    assert config.workflow.orchestration.normal_frontier_owner == "wrc"
    assert config.orchestrator.backend == "codex"
    assert config.runtime.run_manager.backend == "codex"
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

    orchestrator = next(
        role for role in config.roles if role.name == "orchestrator"
    )
    assert "orchestrator.semantic.checkpoint.requested" in orchestrator.triggers
    assert orchestrator.backend == "codex"
    assert orchestrator.execution.default_profile == "bounded-direct-v1"
