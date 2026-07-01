from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.config.schema import (
    FanoutChildConfig,
    WorkflowStageConfig,
    WorkflowStageCriteriaConfig,
    WorkflowStageOutputConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.stage_contract import evaluate_stage_contract


def test_loader_parses_workflow_stage_criteria(tmp_path: Path) -> None:
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        """
project:
  name: demo
roles:
  - name: dev
    role_kind: writer
workflow:
  stages:
    - id: implement-wave
      trigger: dev.build.done
      topology: fanout_writer_scoped
      roles: [dev]
      task_map: task-map.json
      criteria:
        output:
          required_keys: [summary, verification.command]
          required_artifacts: [build/report.json]
          artifact_kinds: [implementation_plan]
        success_criteria:
          - kind: event_exists
            event_type: dev.build.done
        retry:
          max_attempts: 2
          backoff_seconds: 30
          on_failure: retry
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path)
    stage = cfg.workflow.stages[0]

    assert stage.criteria.output.required_keys == ["summary", "verification.command"]
    assert stage.criteria.output.required_artifacts == ["build/report.json"]
    assert stage.criteria.output.artifact_kinds == ["implementation_plan"]
    assert stage.criteria.retry.max_attempts == 2
    assert stage.criteria.retry.on_failure == "retry"


def test_stage_contract_passes_when_outputs_and_criteria_match(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    artifact = tmp_path / "build" / "report.json"
    artifact.parent.mkdir()
    artifact.write_text("{}\n", encoding="utf-8")
    task = Task(id="TASK-1", title="impl", contract=TaskContract())
    stage = WorkflowStageConfig(
        id="impl",
        trigger="dev.build.done",
        topology="fanout_writer_scoped",
        children=[FanoutChildConfig(role="dev", scope="src/**")],
        criteria=WorkflowStageCriteriaConfig(
            output=WorkflowStageOutputConfig(
                required_keys=["summary", "verification.command"],
                required_artifacts=["build/report.json"],
                artifact_kinds=["implementation_plan"],
            ),
            success_criteria=[{"kind": "event_exists", "event_type": "dev.build.done"}],
        ),
    )
    events = [ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "summary": "done",
            "verification": {"command": "pytest"},
            "artifact_refs": [{"kind": "implementation_plan", "path": "plan.md"}],
        },
    )]

    result = evaluate_stage_contract(
        stage=stage,
        task=task,
        events=events,
        state_dir=state_dir,
        project_root=tmp_path,
    )

    assert result.passed is True
    assert result.missing_output_keys == []
    assert result.missing_artifact_kinds == []


def test_stage_contract_reports_missing_output_keys(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task = Task(id="TASK-1", title="impl", contract=TaskContract())
    stage = WorkflowStageConfig(
        id="impl",
        trigger="dev.build.done",
        topology="fanout_writer_scoped",
        children=[FanoutChildConfig(role="dev", scope="src/**")],
        criteria=WorkflowStageCriteriaConfig(
            output=WorkflowStageOutputConfig(required_keys=["summary"]),
        ),
    )
    events = [ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={},
    )]

    result = evaluate_stage_contract(
        stage=stage,
        task=task,
        events=events,
        state_dir=state_dir,
        project_root=tmp_path,
    )

    assert result.passed is False
    assert result.missing_output_keys == ["summary"]
