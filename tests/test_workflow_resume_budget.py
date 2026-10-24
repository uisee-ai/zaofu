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
