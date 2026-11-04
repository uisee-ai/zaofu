"""A suspended synth cannot fail while awaiting its authorized replay."""
import pytest

from tests.test_reader_respawn_continuation import _running_recovered_synth
from zf.core.events import EventWriter, ZfEvent
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_resume_apply import apply_workflow_resume


@pytest.mark.parametrize('admitted', [False, True])
def test_suspended_synth_with_old_loss_waits_for_or_replays_checkpoint(tmp_path, admitted):
    state, log, runtime, role, dispatch, _ = _running_recovered_synth(tmp_path)
    payload = dispatch.payload
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=EventWriter(log))
    service.interrupt(operation_id=payload['operation_id'], request_hash=payload['request_hash'],
                      workflow_run_id=payload['workflow_run_id'], task_id='', reason='graceful_stop')
    log.append(ZfEvent(type='worker.launch_artifact.written', actor='zf-cli',
                       payload={'instance_id': role.instance_id, 'launch_attempt': 2}))
    if admitted:
        checkpoint = next(c for c in build_workflow_resume_projection(state, runtime.config)['operation_checkpoints']
                          if c['operation_id'] == payload['operation_id'])
        assert apply_workflow_resume(state, runtime.config, checkpoint_id=checkpoint['checkpoint_id'])['applied'] == 1
    before = len(runtime.transport.sent)
    result = runtime._recover_lost_fanout_synth_dispatches(log.read_all())
    failures = [e for e in log.read_all() if e.type == 'fanout.synth.completed' and e.payload.get('status') == 'failed']
    assert not failures
    assert result is admitted
    assert len(runtime.transport.sent) == before + int(admitted)
    operation = reduce_workflow_operations(log.read_all())[payload['operation_id']]
    assert operation['status'] == ('running' if admitted else 'suspended')
    assert operation['request_hash'] == payload['request_hash']
    assert operation['active_attempt_id'] == payload['attempt_id']


@pytest.mark.parametrize('fault', ['untrusted_origin', 'wrong_attempt', 'wrong_owner'])
def test_unbound_redrive_does_not_reset_transport_retry_budget(tmp_path, fault):
    state, log, runtime, role, dispatch, _ = _running_recovered_synth(tmp_path)
    p = dispatch.payload
    WorkflowOperationService(state_dir=state, event_log=log, event_writer=EventWriter(log)).interrupt(
        operation_id=p['operation_id'], request_hash=p['request_hash'],
        workflow_run_id=p['workflow_run_id'], task_id='', reason='graceful_stop')
    log.append(ZfEvent(type='worker.launch_artifact.written', actor='zf-cli',
                       payload={'instance_id': role.instance_id, 'launch_attempt': 2}))
    redrive = {'operation_id': p['operation_id'], 'request_hash': p['request_hash'],
               'workflow_run_id': p['workflow_run_id'],
               'source_attempt_id': 'other' if fault == 'wrong_attempt' else p['attempt_id'],
               'recovery_decision_owner': 'other' if fault == 'wrong_owner' else 'kernel_replay',
               'recovery_from_status': 'suspended'}
    log.append(ZfEvent(type='workflow.operation.redrive_admitted', actor='zf-cli',
                       origin='user' if fault == 'untrusted_origin' else 'kernel', payload=redrive))
    before = len(runtime.transport.sent)
    runtime._recover_lost_fanout_synth_dispatches(log.read_all())
    assert len(runtime.transport.sent) == before
