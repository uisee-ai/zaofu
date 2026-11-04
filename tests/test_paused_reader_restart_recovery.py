"""Paused runs and failed dispatches must not exhaust reader recovery."""

import pytest

from tests.test_reader_fanout_runtime import _durable_reader_state
from tests.test_reader_respawn_continuation import _running_recovered_synth
from zf.core.events.writer import EventWriter
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_operation import WorkflowOperationService, load_workflow_operation


def _event(log, kind, **payload):
    log.append(ZfEvent(type=kind, actor="zf-cli", payload=payload))


@pytest.mark.parametrize("kind", ["child", "synth"])
def test_paused_run_restart_does_not_consume_recovery(tmp_path, kind):
    _state, log, runtime, role, dispatch, _before = _running_recovered_synth(
        tmp_path, kind=kind
    )
    run_id = dispatch.payload.get("workflow_run_id") or dispatch.correlation_id
    _event(log, "run.paused", run_id=run_id)
    _event(log, "worker.launch_artifact.written", instance_id=role.instance_id, launch_attempt=2)
    baseline = len(log.read_all())
    recover = (runtime._recover_lost_fanout_synth_dispatches if kind == "synth"
               else runtime._recover_lost_reader_fanout_dispatches)
    assert not recover(log.read_all())
    assert not recover(log.read_all())
    unexpected = {"fanout.child.dispatch_lost", "fanout.child.failed", "fanout.synth.completed", "workflow.operation.failed"}
    assert not [e.type for e in log.read_all()[baseline:] if e.type in unexpected]


def test_blocked_retry_does_not_count_as_delivered_recovery(tmp_path, monkeypatch):
    _state, log, _transport, runtime, _config, child = _durable_reader_state(tmp_path)
    _event(log, "worker.launch_artifact.written", instance_id=child["role_instance"], launch_attempt=2)
    original = runtime._retry_fanout_child
    monkeypatch.setattr(runtime, "_retry_fanout_child", lambda **_kwargs: False)
    assert runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    monkeypatch.setattr(runtime, "_retry_fanout_child", original)
    assert runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    events = log.read_all()
    assert len([e for e in events if e.type == "fanout.child.dispatch_lost"]) == 1
    assert not [e for e in events if e.type == "fanout.child.failed"]
    assert len([e for e in events if e.type == "fanout.child.dispatched"]) == 2


def test_resume_after_paused_restart_reuses_original_request(tmp_path):
    state, log, _transport, runtime, _config, child = _durable_reader_state(tmp_path)
    identity = child["payload"]
    initial = load_workflow_operation(log, identity["operation_id"])
    _event(log, "run.paused", run_id="run-durable")
    WorkflowOperationService(state_dir=state, event_log=log, event_writer=EventWriter(log)).interrupt(
        operation_id=identity["operation_id"], request_hash=identity["request_hash"],
        workflow_run_id="run-durable", reason="graceful_stop", task_id="T-VERIFY",
        source_attempt_id=initial["active_attempt_id"],
    )
    _event(log, "worker.launch_artifact.written", instance_id=child["role_instance"], launch_attempt=2)
    assert not runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    _event(log, "run.resumed", run_id="run-durable")
    assert runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    resumed = load_workflow_operation(log, identity["operation_id"])
    assert resumed["status"] == "running"
    for key in ("request_hash", "active_attempt_id", "lease_id"):
        assert resumed[key] == initial[key]
    assert not [e for e in log.read_all() if e.type == "fanout.child.failed"]


def test_pausing_another_run_does_not_block_reader_recovery(tmp_path):
    _state, log, _transport, runtime, _config, child = _durable_reader_state(tmp_path)
    _event(log, "run.paused", run_id="another-run")
    _event(log, "worker.launch_artifact.written", instance_id=child["role_instance"], launch_attempt=2)
    assert runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    assert len([e for e in log.read_all() if e.type == "fanout.child.dispatched"]) == 2


def test_paused_run_does_not_timeout_while_controller_is_running(tmp_path, monkeypatch):
    _state, log, _transport, runtime, _config, _child = _durable_reader_state(tmp_path)
    _event(log, "run.paused", run_id="run-durable")
    monkeypatch.setattr(runtime, "_now", lambda: 9_999_999_999.0)
    baseline = len(log.read_all())
    runtime._check_fanout_timeouts()
    assert not [e.type for e in log.read_all()[baseline:]
                if e.type in {"fanout.child.failed", "fanout.timed_out", "fanout.synth.completed"}]


def test_resumed_run_gets_bounded_timeout_window(tmp_path, monkeypatch):
    import time
    from datetime import datetime, timezone

    _state, log, _transport, runtime, _config, _child = _durable_reader_state(tmp_path)
    base = time.time()
    for kind, seconds in (("run.paused", 10), ("run.resumed", 10000)):
        log.append(ZfEvent(type=kind, actor="zf-cli", payload={"run_id": "run-durable"},
                           ts=datetime.fromtimestamp(base + seconds, timezone.utc).isoformat()))
    monkeypatch.setattr(runtime, "_now", lambda: base + 10001)
    runtime._check_fanout_timeouts()
    assert not [e for e in log.read_all() if e.type == "fanout.child.failed"]
    monkeypatch.setattr(runtime, "_now", lambda: base + 20000)
    runtime._check_fanout_timeouts()
    assert [e for e in log.read_all() if e.type == "fanout.child.failed"]


def test_synth_timeout_waits_for_run_resume(tmp_path, monkeypatch):
    import time
    from datetime import datetime, timezone

    _state, log, runtime, _role, dispatch, _before = _running_recovered_synth(tmp_path)
    run_id = dispatch.payload.get("workflow_run_id") or dispatch.correlation_id
    base = time.time()
    for kind, seconds in (("run.paused", 10), ("run.resumed", 10000)):
        if kind == "run.resumed":
            monkeypatch.setattr(runtime, "_now", lambda: base + 9999)
            runtime._check_fanout_timeouts()
            assert not [e for e in log.read_all() if e.type == "fanout.synth.completed"]
        log.append(ZfEvent(type=kind, actor="zf-cli", payload={"run_id": run_id},
                           ts=datetime.fromtimestamp(base + seconds, timezone.utc).isoformat()))
    monkeypatch.setattr(runtime, "_now", lambda: base + 10001)
    runtime._check_fanout_timeouts()
    assert not [e for e in log.read_all() if e.type == "fanout.synth.completed"]
    monkeypatch.setattr(runtime, "_now", lambda: base + 20000)
    runtime._check_fanout_timeouts()
    assert [e for e in log.read_all() if e.type == "fanout.synth.completed"]


def test_pausing_another_run_does_not_freeze_timeout(tmp_path, monkeypatch):
    _state, log, _transport, runtime, _config, _child = _durable_reader_state(tmp_path)
    _event(log, "run.paused", run_id="another-run")
    monkeypatch.setattr(runtime, "_now", lambda: 9_999_999_999.0)
    runtime._check_fanout_timeouts()
    assert [e for e in log.read_all() if e.type == "fanout.child.failed"]
