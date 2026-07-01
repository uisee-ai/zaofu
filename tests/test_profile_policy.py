from __future__ import annotations

from zf.core.config.schema import (
    ProjectConfig,
    WorkflowConfig,
    WorkflowFastPathConfig,
    WorkflowStrictTriggersConfig,
    ZfConfig,
)
from zf.core.task.schema import Task, TaskContract
from zf.runtime.profile_policy import gate_policy_for_task


def _config(
    *,
    profile: str = "baseline",
    fast_path: WorkflowFastPathConfig | None = None,
) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="demo"),
        workflow=WorkflowConfig(
            harness_profile=profile,
            fast_path=fast_path or WorkflowFastPathConfig(),
            strict_triggers=WorkflowStrictTriggersConfig(
                file_globs=["src/zf/runtime/**"],
                rework_attempts_gte=2,
            ),
        ),
        roles=[],
    )


def test_baseline_policy_skips_llm_heavy_gates() -> None:
    task = Task(id="TASK-1", title="small", contract=TaskContract())

    policy = gate_policy_for_task(task, config=_config())

    assert policy.effective_profile == "baseline"
    assert "implement" in policy.required_stages
    assert "test" in policy.required_stages
    assert "judge" in policy.skipped_stages
    assert policy.audit_required is True


def test_strict_trigger_promotes_runtime_scope_to_strict() -> None:
    task = Task(
        id="TASK-1",
        title="runtime",
        contract=TaskContract(affected_files=["src/zf/runtime/orchestrator.py"]),
    )

    policy = gate_policy_for_task(task, config=_config())

    assert policy.effective_profile == "strict"
    assert "judge" in policy.required_stages
    assert any("file_glob:src/zf/runtime/**" in item for item in policy.promotion_reasons)


def test_fast_path_skips_configured_stages_in_strict_project() -> None:
    task = Task(
        id="TASK-1",
        title="docs typo",
        contract=TaskContract(scope=["docs/readme.md"]),
    )
    cfg = _config(
        profile="strict",
        fast_path=WorkflowFastPathConfig(
            enabled=True,
            max_scope_files=2,
            skip_stages=["design", "judge"],
            blocked_file_globs=["src/zf/runtime/**"],
        ),
    )

    policy = gate_policy_for_task(task, config=cfg)

    assert policy.effective_profile == "strict"
    assert "design" in policy.skipped_stages
    assert "judge" in policy.skipped_stages
    assert "judge" not in policy.required_stages
