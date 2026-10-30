from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

import zf.runtime.workflow_budget_guard as workflow_budget_guard
from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import (
    ExecutionConfig,
    ExecutionProfileConfig,
    ExecutionProfileLimitsConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowRunLimitsConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.runtime.call_result_runtime import (
    mark_call_operation_started,
    prepare_call_operation,
)
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_attempt_operation_settlement import (
    settle_terminal_operation_attempt,
)
from zf.runtime.workflow_anchor import mark_workflow_managed_task
from zf.runtime.workflow_budget_guard import (
    enforce_active_workflow_budgets,
    usage_meter_snapshot,
)
from zf.runtime.workflow_operation import load_workflow_operation
from zf.runtime.workflow_task_lifecycle import (
    settle_workflow_managed_task_from_run_terminal,
)


class _BudgetTransport:
    def __init__(self) -> None:
        self.terminated: list[str] = []

    def is_alive(self, role_name: str) -> bool:
        return role_name not in self.terminated

    def terminate(self, role_name: str) -> None:
        self.terminated.append(role_name)

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""

    def poll_events(self) -> list[ZfEvent]:
        return []


def _runtime(
    tmp_path: Path,
    *,
    operation_limits: ExecutionProfileLimitsConfig,
    run_limits: WorkflowRunLimitsConfig | None = None,
) -> tuple[Orchestrator, _BudgetTransport]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    profile = ExecutionProfileConfig(limits=operation_limits)
    role = RoleConfig(
        name="dev",
        backend="mock",
        instance_id="dev",
        execution=ExecutionConfig(
            default_profile="bounded-direct-v1",
            profile_allowlist=["bounded-direct-v1"],
        ),
    )
    config = ZfConfig(
        project=ProjectConfig(name="budget-guard"),
        roles=[role],
        workflow=WorkflowConfig(
            execution_profiles={"bounded-direct-v1": profile},
            run_limits=run_limits or WorkflowRunLimitsConfig(),
        ),
    )
    transport = _BudgetTransport()
    runtime = Orchestrator(
        state_dir,
        config,
        transport,  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    return runtime, transport


def _start_operation(
    runtime: Orchestrator,
    *,
    run_id: str = "run-budget-1",
    task_id: str = "TASK-BUDGET",
) -> tuple[str, str]:
    runtime.task_store.add(mark_workflow_managed_task(Task(
        id=task_id,
        title="bounded operation",
        status="in_progress",
    )))
    payload = {
        "workflow_run_id": run_id,
        "role_instance": "dev",
        "stage_id": "impl",
        "child_id": "dev",
        "run_id": "dispatch-budget-1",
        "task_id": task_id,
        "canonical_success_event": "dev.build.done",
        "canonical_failure_event": "dev.blocked",
    }
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="dev",
        stage_id="scan",
        task_id=task_id,
        dispatch_id="dispatch-budget-1",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id=task_id,
        dispatch_id="dispatch-budget-1",
    )
    attempt_store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    attempt = attempt_store.ensure_for_dispatch(
        run_id=run_id,
        task_id=task_id,
        dispatch_id="dispatch-budget-1",
        role="dev",
        instance_id="dev",
        operation_id=prepared.operation_id,
        briefing_ref="briefings/dev.md",
        created_at="2026-08-01T00:00:00+00:00",
        lease_expires_at="2099-08-01T00:00:00+00:00",
        max_attempts=3,
    ).attempt
    return prepared.operation_id, str(attempt["attempt_id"])


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def test_loader_reads_run_limits_and_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text(
        """version: '1.0'
project: {name: budget}
workflow:
  run_limits:
    timeout_seconds: 30
    token_budget: 1000
    cost_budget_usd: 2.5
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.workflow.run_limits == WorkflowRunLimitsConfig(
        timeout_seconds=30,
        token_budget=1000,
        cost_budget_usd=2.5,
    )
    path.write_text(
        """version: '1.0'
project: {name: budget}
workflow:
  run_limits: {token_buget: 1000}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="token_buget"):
        load_config(path)


def test_run_token_budget_supports_long_horizon_aggregate_usage() -> None:
    assert WorkflowRunLimitsConfig(token_budget=100_000_000).token_budget == (
        100_000_000
    )
    with pytest.raises(ValueError, match="between 0 and 100000000"):
        WorkflowRunLimitsConfig(token_budget=100_000_001)


def test_operation_budget_cancels_provider_and_blocks_attempt(
    tmp_path: Path,
) -> None:
    runtime, transport = _runtime(
        tmp_path,
        operation_limits=ExecutionProfileLimitsConfig(
            timeout_seconds=1,
            max_usage_samples=1,
            token_budget=10,
            cost_budget_usd=0.000001,
        ),
    )
    operation_id, attempt_id = _start_operation(runtime)
    operation = load_workflow_operation(runtime.event_log, operation_id)
    assert operation is not None
    runtime.cost_tracker.record_usage(
        "dev",
        10,
        10,
        instance_id="dev",
        source_event_id="usage-budget-1",
    )

    emitted = enforce_active_workflow_budgets(
        runtime,
        now_epoch=_epoch(str(operation["started_at"])) + 1,
    )

    budget = next(event for event in emitted if event.type == "workflow.budget.exceeded")
    assert budget.payload["scope"] == "operation"
    assert budget.payload["exceeded_dimensions"] == [
        "wall_clock",
        "usage_samples",
        "tokens",
        "usd",
    ]
    blocked = next(event for event in emitted if event.type == "workflow.operation.blocked")
    assert transport.terminated == ["dev"]
    assert load_workflow_operation(runtime.event_log, operation_id)["status"] == "blocked"
    assert settle_terminal_operation_attempt(runtime, blocked)
    assert TaskAttemptStore(
        runtime.state_dir / "task_attempts.json"
    ).get(attempt_id)["status"] == "failed"
    assert any(event.type == "run.goal.blocked" for event in emitted)


def test_operation_budget_uses_per_dimension_cumulative_deltas(
    tmp_path: Path,
) -> None:
    runtime, transport = _runtime(
        tmp_path,
        operation_limits=ExecutionProfileLimitsConfig(
            token_budget=1_500_000,
        ),
    )
    _start_operation(runtime)
    for index in range(1, 21):
        runtime.cost_tracker.record_cumulative_usage(
            role="dev",
            instance_id="dev",
            input_tokens=index * 70_000,
            output_tokens=1_000 if index % 2 else 500,
            usage_series_id="codex:budget-regression",
            usage_sample_id=f"sample-{index}",
        )

    emitted = enforce_active_workflow_budgets(runtime)

    assert not any(
        event.type == "workflow.budget.exceeded"
        for event in emitted
    )
    totals = runtime.cost_tracker.per_role_totals()["dev"]
    assert totals.input_tokens == 1_400_000
    assert totals.output_tokens == 10_500
    assert transport.terminated == []


def test_run_timeout_cancels_active_operation_and_blocks_parent_task(
    tmp_path: Path,
) -> None:
    runtime, transport = _runtime(
        tmp_path,
        operation_limits=ExecutionProfileLimitsConfig(),
        run_limits=WorkflowRunLimitsConfig(timeout_seconds=5),
    )
    run_id = "run-budget-timeout"
    operation_id, _attempt_id = _start_operation(runtime, run_id=run_id)
    admitted = runtime.event_writer.append(ZfEvent(
        type="run.admission.admitted",
        actor="orchestrator",
        task_id="TASK-BUDGET",
        payload={
            "run_id": run_id,
            "workflow_run_id": run_id,
            "request_id": run_id,
            "task_id": "TASK-BUDGET",
            "source_event_id": "invoke-budget-timeout",
            "budget_snapshot": usage_meter_snapshot(runtime),
            "run_limits": {
                "timeout_seconds": 5,
                "token_budget": 0,
                "cost_budget_usd": 0,
            },
        },
        correlation_id=run_id,
    ))

    emitted = enforce_active_workflow_budgets(
        runtime,
        now_epoch=_epoch(admitted.ts) + 5,
    )

    cancelled = next(
        event for event in emitted if event.type == "workflow.operation.cancelled"
    )
    terminal = next(event for event in emitted if event.type == "run.goal.blocked")
    assert transport.terminated == ["dev"]
    assert load_workflow_operation(runtime.event_log, operation_id)["status"] == "cancelled"
    assert cancelled.payload["reason"] == "workflow_budget_exceeded:wall_clock"
    settled = settle_workflow_managed_task_from_run_terminal(
        task_store=runtime.task_store,
        event_writer=runtime.event_writer,
        terminal_event=terminal,
    )
    assert settled is not None
    assert runtime.task_store.get("TASK-BUDGET").status == "blocked"


def test_research_result_terminal_stops_run_and_operation_budget_watchdogs(
    tmp_path: Path,
) -> None:
    runtime, transport = _runtime(
        tmp_path,
        operation_limits=ExecutionProfileLimitsConfig(timeout_seconds=5),
        run_limits=WorkflowRunLimitsConfig(timeout_seconds=5),
    )
    run_id = "run-research-terminal"
    operation_id, _attempt_id = _start_operation(runtime, run_id=run_id)
    admitted = runtime.event_writer.append(ZfEvent(
        type="run.admission.admitted",
        actor="orchestrator",
        task_id="TASK-BUDGET",
        correlation_id=run_id,
        payload={
            "run_id": run_id,
            "workflow_run_id": run_id,
            "request_id": run_id,
            "task_id": "TASK-BUDGET",
            "source_event_id": "invoke-research-terminal",
            "budget_snapshot": usage_meter_snapshot(runtime),
            "run_limits": {
                "timeout_seconds": 5,
                "token_budget": 0,
                "cost_budget_usd": 0,
            },
        },
    ))
    runtime.event_writer.append(ZfEvent(
        type="workflow.result.available",
        actor="zf-cli",
        task_id="TASK-BUDGET",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "result_kind": "research_report",
            "status": "available",
        },
    ))

    emitted = enforce_active_workflow_budgets(
        runtime,
        now_epoch=_epoch(admitted.ts) + 10,
    )

    assert emitted == []
    assert transport.terminated == []
    assert load_workflow_operation(runtime.event_log, operation_id)["status"] == (
        "running"
    )


def test_run_budget_uses_admission_limits_after_config_changes(
    tmp_path: Path,
) -> None:
    runtime, transport = _runtime(
        tmp_path,
        operation_limits=ExecutionProfileLimitsConfig(),
        run_limits=WorkflowRunLimitsConfig(timeout_seconds=5),
    )
    run_id = "run-budget-pinned"
    operation_id, _attempt_id = _start_operation(runtime, run_id=run_id)
    admitted = runtime.event_writer.append(ZfEvent(
        type="run.admission.admitted",
        actor="orchestrator",
        task_id="TASK-BUDGET",
        payload={
            "run_id": run_id,
            "workflow_run_id": run_id,
            "request_id": run_id,
            "task_id": "TASK-BUDGET",
            "source_event_id": "invoke-budget-pinned",
            "budget_snapshot": usage_meter_snapshot(runtime),
            "run_limits": {
                "timeout_seconds": 5,
                "token_budget": 0,
                "cost_budget_usd": 0,
            },
        },
        correlation_id=run_id,
    ))
    runtime.config.workflow.run_limits = WorkflowRunLimitsConfig()

    emitted = enforce_active_workflow_budgets(
        runtime,
        now_epoch=_epoch(admitted.ts) + 5,
    )

    assert transport.terminated == ["dev"]
    assert load_workflow_operation(runtime.event_log, operation_id)["status"] == "cancelled"
    budget = next(event for event in emitted if event.type == "workflow.budget.exceeded")
    assert budget.payload["limits"]["timeout_seconds"] == 5.0


def test_active_run_budget_amendment_rearms_guard_without_resetting_usage(
    tmp_path: Path,
) -> None:
    runtime, transport = _runtime(
        tmp_path,
        operation_limits=ExecutionProfileLimitsConfig(),
        run_limits=WorkflowRunLimitsConfig(cost_budget_usd=2),
    )
    run_id = "run-budget-amended"
    runtime.event_writer.append(ZfEvent(
        type="run.admission.admitted",
        actor="orchestrator",
        task_id="TASK-BUDGET",
        correlation_id=run_id,
        payload={
            "run_id": run_id,
            "workflow_run_id": run_id,
            "request_id": run_id,
            "task_id": "TASK-BUDGET",
            "source_event_id": "invoke-budget-amended",
            "budget_snapshot": usage_meter_snapshot(runtime),
            "run_limits": {
                "timeout_seconds": 5,
                "token_budget": 10,
                "cost_budget_usd": 2,
            },
        },
    ))
    runtime.cost_tracker.record_usage(
        "dev", 100, 10, provider_cost_usd=2.1,
    )
    first = enforce_active_workflow_budgets(runtime)
    assert any(event.type == "workflow.budget.exceeded" for event in first)
    runtime.event_writer.append(ZfEvent(
        type="run.goal.updated",
        actor="operator",
        correlation_id=run_id,
        payload={
            "run_id": run_id,
            "workflow_run_id": run_id,
            "status": "active",
            "run_limits_patch": {
                "timeout_seconds": 0,
                "token_budget": 0,
                "cost_budget_usd": 3,
            },
        },
    ))

    warnings = enforce_active_workflow_budgets(runtime)
    assert [event.type for event in warnings] == ["workflow.budget.warning"]
    assert warnings[0].payload["warning_level"] == 50
    runtime.cost_tracker.record_usage(
        "dev", 100, 10, provider_cost_usd=1.0,
    )
    second = enforce_active_workflow_budgets(runtime)

    amended = next(
        event for event in second if event.type == "workflow.budget.exceeded"
    )
    assert amended.payload["limits"] == {
        "timeout_seconds": 0.0,
        "max_usage_samples": 0,
        "token_budget": 0,
        "cost_budget_usd": 3.0,
    }
    assert amended.payload["measurement"]["total_usd"] == 3.1
    assert amended.payload["budget_revision_event_id"]


def test_budget_guard_builds_run_aliases_once_per_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _transport = _runtime(
        tmp_path,
        operation_limits=ExecutionProfileLimitsConfig(),
        run_limits=WorkflowRunLimitsConfig(timeout_seconds=60),
    )
    admitted = None
    for index in range(2):
        run_id = f"run-budget-alias-{index}"
        admitted = runtime.event_writer.append(ZfEvent(
            type="run.admission.admitted",
            actor="orchestrator",
            correlation_id=run_id,
            payload={
                "run_id": run_id,
                "workflow_run_id": run_id,
                "request_id": run_id,
                "task_id": f"TASK-BUDGET-{index}",
                "source_event_id": f"invoke-budget-alias-{index}",
                "budget_snapshot": usage_meter_snapshot(runtime),
                "run_limits": {
                    "timeout_seconds": 60,
                    "token_budget": 0,
                    "cost_budget_usd": 0,
                },
            },
        ))
    assert admitted is not None
    calls = 0
    original = workflow_budget_guard.run_aliases

    def counting_run_aliases(events: object) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return original(events)  # type: ignore[arg-type]

    monkeypatch.setattr(
        workflow_budget_guard,
        "run_aliases",
        counting_run_aliases,
    )

    assert enforce_active_workflow_budgets(
        runtime,
        now_epoch=_epoch(admitted.ts),
    ) == []
    assert calls == 1


def test_budget_guard_is_idempotent_below_and_after_limit(tmp_path: Path) -> None:
    runtime, transport = _runtime(
        tmp_path,
        operation_limits=ExecutionProfileLimitsConfig(token_budget=100),
    )
    operation_id, _attempt_id = _start_operation(runtime)
    operation = load_workflow_operation(runtime.event_log, operation_id)
    assert operation is not None
    runtime.cost_tracker.record_usage("dev", 10, 5, instance_id="dev")

    assert enforce_active_workflow_budgets(
        runtime,
        now_epoch=_epoch(str(operation["started_at"])) + 1,
    ) == []
    runtime.cost_tracker.record_usage("dev", 100, 0, instance_id="dev")
    first = enforce_active_workflow_budgets(
        runtime,
        now_epoch=_epoch(str(operation["started_at"])) + 2,
    )
    second = enforce_active_workflow_budgets(
        runtime,
        now_epoch=_epoch(str(operation["started_at"])) + 3,
    )

    assert any(event.type == "workflow.budget.exceeded" for event in first)
    assert second == []
    assert transport.terminated == ["dev"]


def test_operation_usage_sample_limit_is_relative_to_start_baseline(
    tmp_path: Path,
) -> None:
    runtime, transport = _runtime(
        tmp_path,
        operation_limits=ExecutionProfileLimitsConfig(max_usage_samples=2),
    )
    runtime.cost_tracker.record_usage(
        "dev",
        10,
        1,
        instance_id="dev",
        source_event_id="usage-before-operation",
    )
    operation_id, _attempt_id = _start_operation(runtime)
    operation = load_workflow_operation(runtime.event_log, operation_id)
    assert operation is not None

    runtime.cost_tracker.record_usage(
        "dev",
        10,
        1,
        instance_id="dev",
        source_event_id="usage-operation-1",
    )
    warnings = enforce_active_workflow_budgets(
        runtime,
        now_epoch=_epoch(str(operation["started_at"])) + 1,
    )
    assert [event.type for event in warnings] == ["workflow.budget.warning"]
    assert warnings[0].payload["measurement"]["usage_samples"] == 1

    runtime.cost_tracker.record_usage(
        "dev",
        10,
        1,
        instance_id="dev",
        source_event_id="usage-operation-2",
    )
    emitted = enforce_active_workflow_budgets(
        runtime,
        now_epoch=_epoch(str(operation["started_at"])) + 2,
    )

    budget = next(event for event in emitted if event.type == "workflow.budget.exceeded")
    assert budget.payload["exceeded_dimensions"] == ["usage_samples"]
    assert budget.payload["measurement"]["usage_samples"] == 2
    assert budget.payload["measurement"]["baseline"]["entries"] == 1
    assert transport.terminated == ["dev"]
