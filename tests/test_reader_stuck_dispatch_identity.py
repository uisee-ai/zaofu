"""A sibling's newer dispatch cannot strand a live Reader's recovery."""
from types import SimpleNamespace

import pytest

from tests.test_reader_fanout_runtime import _durable_reader_state
from zf.core.events import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.workflow_operation import load_workflow_operation


def _stuck_reader(tmp_path, monkeypatch, *, spawn_fails=False):
    state, log, transport, runtime, config, _child = _durable_reader_state(tmp_path)
    role = config.roles[0]
    dispatch = next(e for e in log.read_all() if e.type == 'fanout.child.dispatched')
    identity = dispatch.payload
    op = load_workflow_operation(log, identity['operation_id'])
    task_id = op['task_id']
    runtime.task_store.add(Task(id=task_id, title='candidate anchor', status='in_progress',
                                assigned_to=role.instance_id, active_dispatch_id='sibling-attempt'))
    runtime.event_writer.emit('run.started', payload={'workflow_run_id': 'run-durable'})
    runtime.event_writer.append(ZfEvent(type='fanout.child.dispatched', actor='zf-cli', origin='kernel',
        task_id=task_id, correlation_id='run-durable', payload={**identity,
        'child_id':'sibling', 'role_instance':'verify-2', 'run_id':'sibling-attempt',
        'attempt_id':'sibling-attempt', 'dispatch_id':'sibling-attempt', 'operation_id':'sibling-op'}))
    runtime.event_writer.append(ZfEvent(type='workflow.operation.started', actor='zf-cli', origin='kernel',
        task_id=task_id, payload={'task_id':task_id, 'role_instance':'verify-2',
        'operation_id':'sibling-op','attempt_id':'sibling-attempt','dispatch_id':'sibling-attempt'}))
    alive = [True]
    stopped = []
    def stop(name):
        stopped.append(name)
        alive[0] = False
    def spawn(_role, **kwargs):
        if spawn_fails:
            raise RuntimeError('mock provider unavailable')
        alive[0] = True
    monkeypatch.setattr(transport, 'terminate', stop, raising=False)
    monkeypatch.setattr(transport, 'is_alive', lambda name: alive[0])
    monkeypatch.setattr(runtime, '_get_spawn_coordinator', lambda: SimpleNamespace(spawn=spawn))
    monkeypatch.setattr(runtime, '_wait_role_ready', lambda role: True)
    return runtime, log, role, dispatch, op, stopped


def test_silent_reader_resumes_own_operation_with_newer_sibling(tmp_path, monkeypatch):
    runtime, log, role, dispatch, op, stopped = _stuck_reader(tmp_path, monkeypatch)
    result = runtime._report_stuck_worker(role, source='heartbeat_sweep', heartbeat_age_s=3000)
    assert result.action == 'respawn'
    assert stopped == [role.instance_id]
    signal = next(e for e in log.read_all() if e.type == 'worker.stuck')
    assert signal.causation_id == dispatch.id
    assert signal.payload['dispatch_id'] == dispatch.payload['run_id']
    assert signal.payload['operation_id'] == op['operation_id']
    assert signal.payload['workflow_run_id'] == op['workflow_run_id']
    recovered = next(e for e in log.read_all() if e.type == 'worker.recovery.injected')
    assert recovered.payload['recovered_dispatch_event_id'] == dispatch.id
    assert recovered.payload['operation_id'] == op['operation_id']
    after = load_workflow_operation(log, op['operation_id'])
    assert after['status'] == 'running'
    for key in ('request_hash','active_attempt_id','request_ref'):
        assert after[key] == op[key]
    assert not any(e.type in {'task.requeued','task.done','verify.child.completed'} for e in log.read_all())
    assert runtime.task_store.get(op['task_id']).active_dispatch_id == 'sibling-attempt'


@pytest.mark.parametrize('guard', ['paused','settled','wrong_hash','circuit','terminal_fanout'])
def test_reader_stuck_preserves_lifecycle_fences(tmp_path, monkeypatch, guard):
    runtime, log, role, dispatch, op, stopped = _stuck_reader(tmp_path, monkeypatch)
    if guard == 'paused':
        runtime.event_writer.emit('run.paused', payload={'workflow_run_id':'run-durable'})
    elif guard == 'terminal_fanout':
        runtime.event_writer.emit('fanout.aggregate.completed', payload={
            'fanout_id':dispatch.payload['fanout_id'], 'status':'failed'})
    elif guard == 'settled':
        runtime.event_writer.emit('workflow.operation.settled', payload={
            'operation_id':op['operation_id'], 'status':'completed'})
    elif guard == 'wrong_hash':
        original = runtime._active_fanout_child_for_instance(role.instance_id)
        monkeypatch.setattr(runtime, '_active_fanout_child_for_instance', lambda name: {**original,'request_hash':'wrong'})
    else:
        runtime._respawn_success_circuit_opened.add(role.instance_id)
    runtime._report_stuck_worker(role, source='heartbeat_sweep', heartbeat_age_s=3000)
    assert not stopped
    assert not any(e.type == 'task.requeued' for e in log.read_all())


def test_reader_respawn_failure_is_bound_to_own_operation(tmp_path, monkeypatch):
    runtime, log, role, dispatch, op, stopped = _stuck_reader(tmp_path, monkeypatch, spawn_fails=True)
    result = runtime._report_stuck_worker(role, source='heartbeat_sweep', heartbeat_age_s=3000)
    assert result.action == 'respawn_failed'
    failed = next(e for e in log.read_all() if e.type == 'worker.respawn.failed')
    assert failed.payload['operation_id'] == op['operation_id']
    assert failed.payload['dispatch_id'] == dispatch.payload['run_id']
    assert failed.payload['attempt_id'] == op['active_attempt_id']
    assert load_workflow_operation(log, op['operation_id'])['status'] == 'running'
