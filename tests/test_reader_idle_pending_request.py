"""A never-delivered reader request must not deadlock its idle provider's pin."""
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from tests.test_role_lifecycle_runtime import _runtime
from zf.core.events import ZfEvent
from zf.runtime.environment_usage import EnvironmentInUse
from zf.runtime.reader_target_dispatch import pin_reader_target_or_defer
from zf.runtime.workflow_operation import WorkflowOperationService, load_workflow_operation
from zf.runtime.workdirs import WorkdirManager


@pytest.mark.parametrize("fault", ["", "deadline", "never_sent_suspended", "running", "suspended", "prior_start", "prior_send",
    "other_attempt", "other_run", "other_stage", "writer", "active_turn", "unknown_transport"])
def test_pending_pin_releases_only_its_never_delivered_idle_provider(tmp_path, monkeypatch, fault):
    runtime, role, transport = _runtime(tmp_path)
    role.role_kind = "reader"
    transport.alive.add(role.instance_id)
    service = WorkflowOperationService(state_dir=runtime.state_dir,
        event_log=runtime.event_log, event_writer=runtime.event_writer)
    service.ensure_operation(workflow_run_id="other" if fault == "other_run" else "run",
        operation_id="op", operation_type="fanout_writer_child" if fault == "writer" else "fanout_reader_child",
        parent_stage_id="other" if fault == "other_stage" else "verify",
        role_instance=role.instance_id, active_attempt_id="other" if fault == "other_attempt" else "attempt",
        request={"prompt": "verify"})
    if fault in {"running", "suspended", "prior_start"}:
        runtime.event_writer.emit("workflow.operation.started", payload={"operation_id": "op"})
        if fault != "running":
            runtime.event_writer.emit("workflow.operation.interrupted", payload={"operation_id": "op"})
        if fault == "prior_start":
            runtime.event_writer.emit("workflow.operation.redrive_admitted", payload={"operation_id": "op"})
    if fault == "prior_send":
        runtime.event_writer.emit("fanout.child.dispatched", payload={"operation_id": "op",
            "role_instance": role.instance_id, "run_id": "attempt"})
    if fault == "never_sent_suspended":
        runtime.event_writer.emit("workflow.operation.interrupted", payload={"operation_id": "op"})
    monkeypatch.setattr(runtime, "_active_provider_turn",
        lambda _: {"active": True} if fault == "active_turn" else None)
    if fault == "unknown_transport":
        monkeypatch.setattr(transport, "is_alive", lambda _: (_ for _ in ()).throw(RuntimeError("unknown")))
    def pin(*args):
        if role.instance_id in transport.alive:
            raise EnvironmentInUse("idle provider owns workspace")
        return "a" * 40
    monkeypatch.setattr(WorkdirManager, "pin_reader_target", pin)
    context = SimpleNamespace(stage_id="verify", trace_id="run", fanout_id="f")
    child = SimpleNamespace(child_id="reader", payload={})
    if fault == "deadline":
        runtime.event_writer.append(ZfEvent(type="fanout.child.dispatch_deferred",
            ts=datetime.fromtimestamp(runtime._now() - role.stuck_threshold_seconds - 61,
                timezone.utc).isoformat(), payload={"fanout_id": "f", "child_id": "reader",
                "run_id": "attempt", "reason": "reader_environment_busy"}))
    args = dict(role=role, target_ref="HEAD", context=context, child=child,
        run_id="attempt", causation_id="start")
    result = pin_reader_target_or_defer(runtime, **args)
    if fault not in {"", "deadline", "never_sent_suspended"}:
        assert not result
        assert not transport.terminated
    else:
        assert result
        assert transport.terminated == [role.instance_id]
        assert not any(e.type == "fanout.child.failed" for e in runtime.event_log.read_all())
        assert pin_reader_target_or_defer(runtime, **args)
        assert child.payload["target_commit"] == "a" * 40
        assert load_workflow_operation(runtime.event_log, "op")["status"] == (
            "suspended" if fault == "never_sent_suspended" else "requested")


@pytest.mark.parametrize('fault', ['', 'foreign_path', 'child_process', 'unknown_probe', 'wrong_pid', 'wrong_role'])
def test_dead_provider_shell_is_released_only_in_its_managed_idle_slot(tmp_path, monkeypatch, fault):
    from zf.runtime.transport import WorkerLifecycleSnapshot
    runtime, role, transport = _runtime(tmp_path)
    role.role_kind = 'reader'
    service = WorkflowOperationService(state_dir=runtime.state_dir,
        event_log=runtime.event_log, event_writer=runtime.event_writer)
    service.ensure_operation(workflow_run_id='run', operation_id='op', operation_type='fanout_reader_child',
        parent_stage_id='verify', role_instance=role.instance_id, active_attempt_id='attempt', request={})
    runtime.event_writer.emit('workflow.operation.interrupted', payload={'operation_id': 'op'})
    workspace = tmp_path / 'managed-reader'
    monkeypatch.setattr(WorkdirManager, 'plan', lambda *_: SimpleNamespace(
        enabled=True, mode='worktree', role_kind='reader', project_path=str(workspace)))
    snapshot = WorkerLifecycleSnapshot(
        role_name='other' if fault == 'wrong_role' else role.instance_id, alive=False,
        pane_pid='123', current_command='bash',
        current_path=str(tmp_path / 'unrelated' if fault == 'foreign_path' else workspace),
        process_probe={'available': fault != 'unknown_probe',
                       'pane_pid': '321' if fault == 'wrong_pid' else '123',
                       'processes': [{'pid': '124', 'command': 'work'}] if fault == 'child_process' else []})
    transport.lifecycle_snapshot = lambda _: snapshot
    def pin(*args):
        if not transport.terminated:
            raise EnvironmentInUse('managed shell cwd holds workspace after provider exit')
        return 'a' * 40
    monkeypatch.setattr(WorkdirManager, 'pin_reader_target', pin)
    args = dict(role=role, target_ref='HEAD', context=SimpleNamespace(
        stage_id='verify', trace_id='run', fanout_id='f'),
        child=SimpleNamespace(child_id='reader', payload={}), run_id='attempt', causation_id='start')
    assert pin_reader_target_or_defer(runtime, **args) is (not bool(fault))
    assert transport.terminated == ([] if fault else [role.instance_id])
    if not fault:
        assert pin_reader_target_or_defer(runtime, **args)
    assert not transport.sent


def test_target_manager_initialization_failure_keeps_dispatch_fenced(tmp_path, monkeypatch):
    runtime, role, transport = _runtime(tmp_path)
    def unavailable(*a, **k):
        raise ValueError("target manager unavailable")
    monkeypatch.setattr(WorkdirManager, "__init__", unavailable)
    assert not pin_reader_target_or_defer(runtime, role=role, target_ref="HEAD",
        context=SimpleNamespace(stage_id="verify", trace_id="run", fanout_id="f"),
        child=SimpleNamespace(child_id="reader", payload={}), run_id="attempt", causation_id="start")
    assert not transport.sent
    failed = [e for e in runtime.event_log.read_all() if e.type == "fanout.child.failed"]
    assert failed[-1].payload["reason"] == "pin_failed: target manager unavailable"
