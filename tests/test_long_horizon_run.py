"""Run age is independent of bounded operation execution time."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_workflow_budget_guard import _runtime, _start_operation
from zf.core.config.schema import ExecutionProfileLimitsConfig, WorkflowRunLimitsConfig
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_budget_guard import enforce_active_workflow_budgets, usage_meter_snapshot


@pytest.mark.parametrize('age_hours', [5, 72])
@pytest.mark.parametrize('operation_seconds,blocked', [(30, False), (1201, True)])
def test_unlimited_run_age_preserves_operation_deadline(
    tmp_path: Path, age_hours: int, operation_seconds: int, blocked: bool,
) -> None:
    runtime, transport = _runtime(
        tmp_path, operation_limits=ExecutionProfileLimitsConfig(timeout_seconds=1200),
        run_limits=WorkflowRunLimitsConfig(timeout_seconds=0, token_budget=0),
    )
    now = datetime.now(timezone.utc)
    runtime.event_writer.append(ZfEvent(
        type='run.admission.admitted', actor='orchestrator',
        ts=(now - timedelta(hours=age_hours)).isoformat(),
        task_id='TASK-BUDGET', correlation_id='run-budget-1', payload={
            'run_id': 'run-budget-1', 'workflow_run_id': 'run-budget-1',
            'request_id': 'run-budget-1', 'task_id': 'TASK-BUDGET',
            'source_event_id': 'invoke-long-horizon',
            'budget_snapshot': usage_meter_snapshot(runtime),
            'run_limits': {'timeout_seconds': 0, 'token_budget': 0, 'cost_budget_usd': 0},
        },
    ))
    operation_id, _ = _start_operation(runtime)
    started = next(
        e for e in runtime.event_log.read_all()
        if e.type == 'workflow.operation.started'
        and e.payload.get('operation_id') == operation_id
    )
    runtime.cost_tracker.record_usage('dev', 20_000_000, 10)
    events = enforce_active_workflow_budgets(
        runtime, now_epoch=datetime.fromisoformat(started.ts).timestamp() + operation_seconds,
    )
    exceeded = [e for e in events if e.type == 'workflow.budget.exceeded']
    assert len(exceeded) == int(blocked)
    if blocked:
        assert exceeded[0].payload['scope_id'] == operation_id
        assert exceeded[0].payload['exceeded_dimensions'] == ['wall_clock']
    else:
        assert events == []
        assert transport.terminated == []
    assert not any(e.type == 'run.goal.completed' for e in runtime.event_log.read_all())
