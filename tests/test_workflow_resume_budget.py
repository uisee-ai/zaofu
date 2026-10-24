"""A supervised stop/resume cannot grant a fresh operation token allowance."""

import pytest

from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_resume_apply import apply_workflow_resume
from tests.test_workflow_operation import _service
from tests.test_workflow_operation_resume import _config


@pytest.mark.parametrize("reserve_again", [False, True])
def test_controlled_restart_preserves_original_operation_budget_baseline(tmp_path, reserve_again):
    (tmp_path / 'kanban.json').write_text('[]')
    service = _service(tmp_path)
    op = service.ensure_operation(workflow_run_id='run-1', operation_id='op-1',
                                  operation_type='task-stage', task_id='T1',
                                  request={'task_pipeline_stage': 'impl', 'operation_generation': 1})
    identity = {'workflow_run_id': 'run-1', 'operation_id': op.operation_id, 'request_hash': op.request_hash}
    original = {'meter_available': True, 'instance_id': 'dev-1', 'total_tokens': 100, 'entries': 1}
    service.mark_started(**identity, task_id='T1', role_instance='dev-1', active_attempt_id='attempt-1',
                         dispatch_id='dispatch-1', budget_snapshot=original)
    first = reduce_workflow_operations(service.event_log.read_all())['op-1']
    service.interrupt(**identity, task_id='T1', reason='graceful_stop', source_attempt_id='attempt-1')
    checkpoint = build_workflow_resume_projection(tmp_path, _config())['operation_checkpoints'][0]
    result = apply_workflow_resume(tmp_path, _config(), checkpoint_id=checkpoint['checkpoint_id'])
    assert result['applied'] == 1
    if reserve_again:
        reservation = service.reserve_continuation(
            **identity, continuation_key='resume-1', expected_generation='GEN-1',
            expected_package_ref='artifacts/package.json', expected_package_digest='a' * 64,
            pending_action_digest='b' * 64, budget_snapshot={**original, 'total_tokens': 1800},
            reservation_expires_at='2099-01-01T00:00:00+00:00', task_id='T1',
        )
        assert reservation.created
    service.mark_started(**identity, task_id='T1', role_instance='dev-1', active_attempt_id='attempt-2',
                         dispatch_id='dispatch-2', budget_snapshot={**original, 'total_tokens': 1800, 'entries': 20})
    resumed = reduce_workflow_operations(service.event_log.read_all())['op-1']
    assert resumed['status'] == 'running'
    assert resumed['active_attempt_id'] == 'attempt-2'
    assert resumed['started_at'] == first['started_at']
    assert resumed['budget_snapshot'] == original


def test_different_budget_meter_scope_is_unavailable_not_zero_usage():
    from zf.runtime.workflow_budget_guard import _exceeded_dimensions, _measurement
    measured = _measurement(
        baseline={'meter_available': True, 'instance_id': 'old-worker', 'total_tokens': 1800},
        current={'meter_available': True, 'instance_id': 'new-worker', 'total_tokens': 10},
        elapsed_seconds=5,
    )
    assert measured['meter_available'] is False
    assert _exceeded_dimensions({'token_budget': 2000}, measured) == ['meter_unavailable']


@pytest.mark.parametrize('field', ['total_tokens', 'entries'])
def test_same_worker_meter_regression_is_unavailable(field):
    from zf.runtime.workflow_budget_guard import _exceeded_dimensions, _measurement
    baseline = {'meter_available': True, 'instance_id': 'dev-1', 'total_tokens': 1800, 'entries': 20}
    current = {**baseline, field: 1}
    measured = _measurement(baseline=baseline, current=current, elapsed_seconds=5)
    assert measured['meter_available'] is False
    assert _exceeded_dimensions({'token_budget': 2000}, measured) == ['meter_unavailable']


def test_sample_only_budget_fails_closed_when_meter_is_unavailable():
    from zf.runtime.workflow_budget_guard import _exceeded_dimensions
    assert _exceeded_dimensions(
        {'max_usage_samples': 10},
        {'meter_available': False, 'usage_samples': 0},
    ) == ['meter_unavailable']


def test_runtime_blocks_and_stops_provider_after_cost_projection_regresses(tmp_path):
    from zf.core.config.schema import ExecutionProfileLimitsConfig
    from zf.runtime.workflow_budget_guard import enforce_active_workflow_budgets
    from tests.test_workflow_budget_guard import _runtime, _start_operation

    runtime, transport = _runtime(tmp_path, operation_limits=ExecutionProfileLimitsConfig(token_budget=2000))
    runtime.cost_tracker.record_usage('dev', 1800, 0, instance_id='dev')
    _start_operation(runtime)
    # Simulate an incomplete cost projection rebuild after a supervised restart.
    runtime.cost_tracker.cost_path.write_text('')
    emitted = enforce_active_workflow_budgets(runtime)
    budget = next(event for event in emitted if event.type == 'workflow.budget.exceeded')
    assert budget.payload['exceeded_dimensions'] == ['meter_unavailable']
    assert budget.payload['scope'] == 'operation'
    assert transport.terminated == ['dev']
    assert any(event.type == 'run.goal.blocked' for event in emitted)


def test_resumed_operation_rearms_its_limit_and_blocks_the_run_again(tmp_path):
    from zf.core.config.schema import ExecutionProfileLimitsConfig
    from zf.core.events.model import ZfEvent
    from zf.runtime.workflow_budget_guard import enforce_active_workflow_budgets, usage_meter_snapshot
    from zf.runtime.workflow_operation import WorkflowOperationService
    from tests.test_workflow_budget_guard import _runtime

    runtime, transport = _runtime(tmp_path, operation_limits=ExecutionProfileLimitsConfig(token_budget=10))
    service = WorkflowOperationService(state_dir=runtime.state_dir, event_log=runtime.event_log,
                                       event_writer=runtime.event_writer)
    op = service.ensure_operation(workflow_run_id='run-1', operation_id='op-1',
        operation_type='task-stage', task_id='T1', request={
            'task_pipeline_stage': 'impl', 'operation_generation': 1,
            'operation_limits': {'token_budget': 10}})
    identity = {'workflow_run_id': 'run-1', 'operation_id': op.operation_id,
                'request_hash': op.request_hash, 'task_id': 'T1'}
    service.mark_started(**identity, role_instance='dev', active_attempt_id='a1', dispatch_id='d1',
                         budget_snapshot=usage_meter_snapshot(runtime, instance_id='dev'))
    runtime.cost_tracker.record_usage('dev', 11, 0, instance_id='dev')
    first = enforce_active_workflow_budgets(runtime)
    assert any(e.type == 'run.goal.blocked' for e in first)
    # The legacy recovery action changes Run limits, not the pinned Operation limit.
    runtime.event_writer.append(ZfEvent(type='run.goal.updated', actor='operator', payload={
        'workflow_run_id': 'run-1', 'run_id': 'run-1', 'source': 'zf_goal_cli',
        'status': 'active', 'run_limits_patch': {'token_budget': 0}}, correlation_id='run-1'))
    checkpoint = build_workflow_resume_projection(runtime.state_dir, runtime.config)['operation_checkpoints'][0]
    assert apply_workflow_resume(runtime.state_dir, runtime.config,
                                 checkpoint_id=checkpoint['checkpoint_id'])['applied'] == 1
    transport.terminated.clear()
    service.mark_started(**identity, role_instance='dev', active_attempt_id='a2', dispatch_id='d2',
                         budget_snapshot=usage_meter_snapshot(runtime, instance_id='dev'))
    second = enforce_active_workflow_budgets(runtime)
    assert any(e.type == 'workflow.budget.exceeded' for e in second)
    assert transport.terminated == ['dev']
    assert any(e.type == 'run.goal.blocked' for e in second)
    assert enforce_active_workflow_budgets(runtime) == []
