"""Reader liveness recovery closes billing authority with the failed child."""
from __future__ import annotations

from zf.core.config.schema import ExecutionConfig, ExecutionProfileConfig, ExecutionProfileLimitsConfig
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_budget_guard import enforce_active_workflow_budgets
from zf.runtime.workflow_operation import load_workflow_operation
from tests.test_reader_fanout_runtime import _start_fanout, _state


def test_exhausted_reader_stops_operation_budget_and_replays_once(tmp_path):
    _state_dir, log, _transport, orch = _state(tmp_path, child_success_event="workflow.child.completed",
                                               child_failure_event="workflow.child.failed")
    orch.config.workflow.execution_profiles['bounded-direct-v1'] = ExecutionProfileConfig(
        limits=ExecutionProfileLimitsConfig(max_usage_samples=2),
    )
    orch.config.workflow.flow_metadata = {"result_protocol": {"mode": "blocking"}}
    for role in orch.config.roles:
        role.execution = ExecutionConfig(default_profile="bounded-direct-v1",
                                         profile_allowlist=["bounded-direct-v1"])
    _start_fanout(orch)
    dispatch = next(e for e in log.read_all() if e.type == 'fanout.child.dispatched'
                    and e.payload['child_id'] == 'review-a')
    operation_id = dispatch.payload['payload']['operation_id']
    for turn_id in ('first', 'retry'):
        closed = ZfEvent(type='provider.turn.closed', actor='review-a', payload={
            'backend': 'codex', 'provider_event': 'task_complete',
            'provider_status': 'completed', 'turn_id': turn_id,
        })
        log.append(closed)
        orch.run_once(events=[closed])
    operation = load_workflow_operation(log, operation_id)
    assert operation['status'] == 'failed'
    assert operation['reason'] == 'provider_turn_closed_without_child_result_recovery_exhausted'
    child_failure = next(e for e in log.read_all() if e.type == 'fanout.child.failed'
                         and e.payload['child_id'] == 'review-a')
    failures = [e for e in log.read_all() if e.type == 'workflow.operation.failed'
                and e.payload['operation_id'] == operation_id]
    assert len(failures) == 1
    assert failures[0].causation_id == child_failure.id
    # Later work by the same role must not charge this completed operation.
    for _ in range(2):
        orch.cost_tracker.record_usage('review-a', 100, 10)
    assert not [e for e in enforce_active_workflow_budgets(orch)
                if e.type == 'workflow.budget.exceeded']
    orch.run_once(events=[])
    assert len([e for e in log.read_all() if e.type == 'workflow.operation.failed'
                and e.payload['operation_id'] == operation_id]) == 1
