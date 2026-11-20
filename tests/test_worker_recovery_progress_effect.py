"""A task-bound process restart is not proof that its original work progressed."""
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.run_manager import _execute_worker_lifecycle_recover, _settle_pending_action_effects
from zf.runtime.recovery_case import build_recovery_case_projection


def requested(tmp_path):
    log = EventLog(tmp_path / 'events.jsonl')
    writer = EventWriter(log, default_origin='kernel')
    action = dict(action='worker-lifecycle-recover', task_id='T1', workflow_run_id='R1',
                  instance_id='dev-1', checkpoint_id='ck', recovery_case_id='case',
                  task_map_generation='g1', effect_timeout_seconds=300,
                  expected_downstream_events=['worker.respawn.requested'])
    assert _execute_worker_lifecycle_recover(writer=writer, action=action, causation_id='fault') == 'applied'
    request = next(e for e in log.read_all() if e.type == 'worker.respawn.requested')
    return log, writer, request


def test_restart_waits_for_admitted_same_work_progress_across_reload(tmp_path):
    log, writer, request = requested(tmp_path)
    writer.emit('worker.respawn.completed', actor='dev-1', causation_id=request.id)
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    assert build_recovery_case_projection(log.read_all())['cases'][0]['status'] == 'verifying'
    for change in ({'workflow_run_id': 'R2'}, {'task_id': 'other'},
                   {'semantic_verdict': 'abstained'}, {'task_map_generation': 'g2'}):
        writer.emit('workflow.call.result.admitted', payload={
            'task_id': 'T1', 'workflow_run_id': 'R1', 'operation_id': 'op-new',
            'task_map_generation': 'g1', 'semantic_verdict': 'passed', **change})
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    writer.emit('workflow.call.result.admitted', payload={
        'task_id': 'T1', 'workflow_run_id': 'R1', 'operation_id': 'op-new',
        'task_map_generation': 'g1', 'semantic_verdict': 'passed'})
    reloaded = EventLog(tmp_path / 'events.jsonl')
    assert _settle_pending_action_effects(reloaded.read_all(), EventWriter(reloaded)) == 1
    assert build_recovery_case_projection(reloaded.read_all())['cases'][0]['status'] == 'resolved'


def test_restart_failure_does_not_wait_for_product_sequence_timeout(tmp_path):
    log, writer, request = requested(tmp_path)
    writer.emit('worker.respawn.failed', actor='dev-1', causation_id=request.id)
    assert _settle_pending_action_effects(log.read_all(), writer) == 1
    assert log.read_all()[-1].type == 'run.manager.action.effect.failed'


def test_fast_result_before_restart_receipt_is_not_lost(tmp_path):
    log, writer, request = requested(tmp_path)
    # Continuation is sent before agent_view_runtime appends respawn.completed.
    writer.emit('workflow.call.result.admitted', payload={
        'task_id': 'T1', 'workflow_run_id': 'R1', 'operation_id': 'op-fast',
        'task_map_generation': 'g1', 'semantic_verdict': 'passed'})
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    writer.emit('worker.respawn.completed', actor='dev-1', causation_id=request.id)
    assert _settle_pending_action_effects(log.read_all(), writer) == 1
