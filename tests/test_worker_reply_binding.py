"""Queued task guidance must not restart a terminal or reassigned operation."""
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.workflow_operation import WorkflowOperationService


class _Transport:
    def terminate(self, role_name):
        pass

    def is_alive(self, role_name):
        return False


def _runtime(tmp_path: Path, monkeypatch):
    state = tmp_path / 'custom-state'
    state.mkdir()
    config = ZfConfig(project=ProjectConfig(name='reply', state_dir='custom-state'),
        roles=[RoleConfig(name='dev', instance_id='dev-lane-2', backend='mock'),
               RoleConfig(name='dev', instance_id='dev-lane-3', backend='mock')])
    runtime = Orchestrator(state, config, _Transport())
    sends = []
    monkeypatch.setattr(runtime, '_send_transport_task', lambda *args: sends.append(args))
    service = WorkflowOperationService(state_dir=state, event_log=runtime.event_log,
                                       event_writer=runtime.event_writer)
    operation = service.ensure_operation(workflow_run_id='run-1', operation_id='op-1',
        operation_type='agent', request={'purpose': 'implement'}, task_id='T1',
        role_instance='dev-lane-2', active_attempt_id='attempt-1')
    identity = dict(operation_id='op-1', request_hash=operation.request_hash,
                    workflow_run_id='run-1', task_id='T1')
    service.mark_started(**identity, dispatch_id='dispatch-1',
                         role_instance='dev-lane-2', active_attempt_id='attempt-1')
    return runtime, service, identity, sends


def test_queued_reply_rejects_operation_that_finishes_before_delivery(tmp_path, monkeypatch):
    runtime, service, identity, sends = _runtime(tmp_path, monkeypatch)
    request = runtime.event_writer.emit('worker.reply.requested', actor='operator',
        task_id='T1', payload={'instance_id': 'dev-lane-2', 'operation_id': 'op-1',
                               'task_id': 'T1', 'message': 'Review the corrected facts.'})
    runtime.enqueue_worker_action_request(request)
    service.block(**identity, reason='upstream_contract_gap')
    runtime._handle_worker_action_requests()
    assert sends == []
    assert not (runtime.state_dir / 'operator-replies').exists()
    failures = [e for e in runtime.event_log.read_all() if e.type == 'worker.reply.failed']
    assert len(failures) == 1 and failures[0].causation_id == request.id
    runtime._handle_worker_action_requests()
    assert sends == []
    assert len([e for e in runtime.event_log.read_all() if e.type == 'worker.reply.failed']) == 1


@pytest.mark.parametrize('delta', [
    {'operation_id': 'missing'}, {'instance_id': 'dev-lane-3'},
    {'task_id': 'T2'}, {'attempt_id': 'attempt-old'}, {'dispatch_id': 'dispatch-old'},
    {'lease_id': 'lease-old'}, {'workflow_run_id': 'run-old'}, {'run_id': 'run-old'},
])
def test_explicit_reply_identity_mismatch_never_falls_back(tmp_path, monkeypatch, delta):
    runtime, _, _, sends = _runtime(tmp_path, monkeypatch)
    request = runtime.event_writer.emit('worker.reply.requested', actor='operator',
        payload={'instance_id': 'dev-lane-2', 'operation_id': 'op-1', 'task_id': 'T1',
                 'message': 'Only the requested attempt may consume this.', **delta})
    runtime._handle_worker_action_requests()
    assert not sends
    assert not (runtime.state_dir / 'operator-replies').exists()
    failures = [e for e in runtime.event_log.read_all() if e.type == 'worker.reply.failed']
    assert len(failures) == 1 and failures[0].causation_id == request.id


@pytest.mark.parametrize('terminal', ['cancel', 'settle'])
def test_cancelled_or_settled_operation_does_not_receive_guidance(tmp_path, monkeypatch, terminal):
    runtime, service, identity, sends = _runtime(tmp_path, monkeypatch)
    if terminal == 'cancel':
        service.cancel(**identity, reason='operator stopped')
    else:
        service.settle(**identity, admitted_call_result_ref={'ref': 'result.json', 'sha256': 'a' * 64})
    runtime.event_writer.emit('worker.reply.requested', actor='operator',
        payload={'instance_id': 'dev-lane-2', 'operation_id': 'op-1', 'message': 'Late reply.'})
    runtime._handle_worker_action_requests()
    assert not sends


def test_current_target_is_delivered_once(tmp_path, monkeypatch):
    runtime, _, _, sends = _runtime(tmp_path, monkeypatch)
    runtime.event_writer.emit('worker.reply.requested', actor='operator',
        payload={'instance_id': 'dev-lane-2', 'operation_id': 'op-1', 'task_id': 'T1',
                 'attempt_id': 'attempt-1', 'dispatch_id': 'dispatch-1',
                 'workflow_run_id': 'run-1', 'message': 'Check the exact current source.'})
    runtime._handle_worker_action_requests()
    runtime._handle_worker_action_requests()
    assert len(sends) == 1
    instance, path, prompt, context = sends[0]
    assert instance == 'dev-lane-2' and 'Check the exact current source.' in path.read_text()
    assert context.task_id == 'T1'
    assert not any(e.type == 'worker.reply.failed' for e in runtime.event_log.read_all())


def test_unbound_resident_reply_remains_supported(tmp_path, monkeypatch):
    runtime, _, _, sends = _runtime(tmp_path, monkeypatch)
    runtime.event_writer.emit('worker.reply.requested', actor='operator',
        payload={'instance_id': 'dev-lane-3', 'message': 'Discuss the proposal.'})
    runtime._handle_worker_action_requests()
    assert len(sends) == 1 and sends[0][3].operation_id is None


def test_admitted_result_is_terminal_for_reply_before_settlement(tmp_path, monkeypatch):
    runtime, _, _, sends = _runtime(tmp_path, monkeypatch)
    runtime.event_writer.emit('workflow.call.result.admitted', actor='kernel',
        payload={'operation_id': 'op-1', 'semantic_verdict': 'failed'})
    runtime.event_writer.emit('worker.reply.requested', actor='operator',
        payload={'instance_id': 'dev-lane-2', 'operation_id': 'op-1', 'message': 'Too late.'})
    runtime._handle_worker_action_requests()
    assert not sends
    assert any(e.type == 'worker.reply.failed' and 'already admitted' in e.payload['reason']
               for e in runtime.event_log.read_all())
