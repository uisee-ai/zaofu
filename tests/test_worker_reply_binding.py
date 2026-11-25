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
    monkeypatch.setattr(runtime.transport, 'send_task',
                        lambda role, path, prompt, *, context: sends.append((role, path, prompt, context)),
                        raising=False)
    service = WorkflowOperationService(state_dir=state, event_log=runtime.event_log,
                                       event_writer=runtime.event_writer)
    operation = service.ensure_operation(workflow_run_id='run-1', operation_id='op-1',
        operation_type='agent', request={'purpose': 'implement'}, task_id='T1',
        role_instance='dev-lane-2', active_attempt_id='attempt-1')
    identity = dict(operation_id='op-1', request_hash=operation.request_hash,
                    workflow_run_id='run-1', task_id='T1')
    service.mark_started(**identity, dispatch_id='dispatch-1', lease_id='lease-1',
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



@pytest.fixture
def web_reply(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from zf.web.server import create_app

    runtime, service, identity, sends = _runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    with TestClient(create_app(runtime.state_dir)) as client:
        def post(binding):
            return client.post(
                "/api/actions/worker-reply",
                headers={"x-zf-web-token": "test-token"},
                json={"instance_id": "dev-lane-2", "message": "Review this.", **binding},
            )
        yield runtime, service, identity, sends, post


@pytest.mark.parametrize("target", ["current", "terminal", "new_attempt", "new_worker",
                                    "paused_run", "resident", "task_context"])
def test_web_worker_reply_delivers_only_to_requested_target(web_reply, target):
    runtime, service, identity, sends, post = web_reply
    binding = {} if target in {"resident", "task_context"} else {
        **identity, "run_id": "run-1", "attempt_id": "attempt-1",
        "dispatch_id": "dispatch-1", "lease_id": "lease-1",
    }
    if target == "task_context":
        binding = {"task_id": "T1"}
    elif target == "paused_run":
        binding = {"operation_id": "op-1"}
    response = post(binding)
    assert response.status_code == 202
    request_id = response.json()["event_id"]
    request = next(e for e in runtime.event_log.read_all() if e.id == request_id)
    assert request.type == "worker.reply.requested" and request.actor == "web"
    assert {k: request.payload.get(k) for k in binding} == binding
    if target == "terminal":
        service.block(**identity, reason="owner paused")
    elif target == "paused_run":
        runtime.event_writer.emit("run.paused", payload={"workflow_run_id": "run-1"})
    elif target in {"new_attempt", "new_worker"}:
        service.mark_started(
            **identity, dispatch_id="dispatch-2", active_attempt_id="attempt-2",
            role_instance="dev-lane-3" if target == "new_worker" else "dev-lane-2",
        )
    runtime._handle_worker_action_requests()
    runtime._handle_worker_action_requests()
    receipts = [e.type for e in runtime.event_log.read_all()
                if e.causation_id == request_id and e.type in {"worker.reply.sent", "worker.reply.failed"}]
    if target in {"current", "resident", "task_context"}:
        assert receipts == ["worker.reply.sent"]
        assert len(sends) == 1 and sends[0][0] == "dev-lane-2"
        if target == "current":
            assert sends[0][3].run_id == "run-1"
    elif target == "paused_run":
        assert receipts == ["worker.reply.failed"] and not sends
        assert any(e.type == "run.dispatch.blocked" and e.payload["reason"] == "run_paused"
                   for e in runtime.event_log.read_all())
    else:
        assert receipts == ["worker.reply.failed"]
        assert not sends and not (runtime.state_dir / "operator-replies").exists()


@pytest.mark.parametrize("attempt_changed", [False, True])
def test_pending_legacy_web_reply_revalidates_nested_binding(tmp_path, monkeypatch, attempt_changed):
    runtime, service, identity, sends = _runtime(tmp_path, monkeypatch)
    payload = {"instance_id": "dev-lane-2", "task_id": "T1", "message": "Queued before upgrade."}
    request = runtime.event_writer.emit("worker.reply.requested", actor="web", payload={
        **payload, "request": {**payload, **identity, "attempt_id": "attempt-1"},
    })
    runtime.enqueue_worker_action_request(request)
    if attempt_changed:
        service.mark_started(**identity, role_instance="dev-lane-2", active_attempt_id="attempt-2",
                             dispatch_id="dispatch-2")
    runtime._handle_worker_action_requests()
    expected = "worker.reply.failed" if attempt_changed else "worker.reply.sent"
    assert len(sends) == (0 if attempt_changed else 1)
    assert any(e.causation_id == request.id and e.type == expected for e in runtime.event_log.read_all())


@pytest.mark.parametrize("field", ["attempt_id", "dispatch_id", "lease_id", "request_hash",
                                   "workflow_run_id", "run_id"])
def test_partial_web_operation_target_fails_closed(web_reply, field):
    runtime, _, _, sends, post = web_reply
    response = post({field: "explicit-target"})
    assert response.status_code == 202
    runtime._handle_worker_action_requests()
    assert not sends
    assert any(e.type == "worker.reply.failed" and e.causation_id == response.json()["event_id"]
               and "operation_id" in e.payload["reason"] for e in runtime.event_log.read_all())


@pytest.mark.parametrize("conflict", [{"attempt_id": "old-attempt"}, {"operation_id": "other"},
                                      {"operation_id": ""}, {"task_id": "other-task"}])
def test_conflicting_recorded_web_targets_are_never_delivered(tmp_path, monkeypatch, conflict):
    runtime, _, identity, sends = _runtime(tmp_path, monkeypatch)
    binding = {**identity, "attempt_id": "attempt-1"}
    request = runtime.event_writer.emit("worker.reply.requested", actor="web", payload={
        "instance_id": "dev-lane-2", "message": "Only for the observed target.",
        **binding, "request": {**binding, **conflict},
    })
    runtime._handle_worker_action_requests()
    assert not sends
    assert any(e.type == "worker.reply.failed" and e.causation_id == request.id
               and "conflict" in e.payload["reason"] for e in runtime.event_log.read_all())


@pytest.mark.parametrize("during_wait", ["paused", "new_attempt", "admitted", "current"])
def test_bound_web_reply_rechecks_recipient_after_ready_wait(web_reply, monkeypatch, during_wait):
    from zf.core.task.schema import Task

    runtime, service, identity, sends, post = web_reply
    runtime.task_store.add(Task(id="T1", title="Active task", status="in_progress",
                                assigned_to="dev-lane-2", active_dispatch_id="dispatch-1"))
    runtime._active_dispatch_ids["T1"] = "dispatch-1"

    def wait_until_ready(role):
        assert role.instance_id == "dev-lane-2"
        if during_wait == "paused":
            runtime.event_writer.emit("run.paused", payload={"workflow_run_id": "run-1"})
        elif during_wait == "new_attempt":
            service.mark_started(**identity, role_instance="dev-lane-2", active_attempt_id="attempt-2",
                                 dispatch_id="dispatch-2", lease_id="lease-2")
        elif during_wait == "admitted":
            runtime.event_writer.emit("workflow.call.result.admitted", actor="kernel",
                                      payload={"operation_id": "op-1", "semantic_verdict": "passed"})
        return True

    monkeypatch.setattr(runtime, "_wait_role_ready", wait_until_ready)
    response = post({"operation_id": "op-1"})
    assert response.status_code == 202
    request_id = response.json()["event_id"]
    runtime._handle_worker_action_requests()
    runtime._handle_worker_action_requests()
    receipts = [event for event in runtime.event_log.read_all()
                if event.causation_id == request_id and event.type in {"worker.reply.sent", "worker.reply.failed"}]
    assert runtime.task_store.get("T1").active_dispatch_id == "dispatch-1"
    assert runtime._active_dispatch_ids["T1"] == "dispatch-1"
    assert len(receipts) == 1
    if during_wait == "current":
        assert receipts[0].type == "worker.reply.sent"
        assert len(sends) == 1
        assert sends[0][3].operation_id == "op-1"
        assert sends[0][3].attempt_id == "attempt-1"
    else:
        assert not sends and receipts[0].type == "worker.reply.failed"
        if during_wait == "paused":
            assert any(event.type == "run.dispatch.blocked" and event.payload["reason"] == "run_paused"
                       for event in runtime.event_log.read_all())
