"""Readiness recovery uses persisted calls and never resumes an operator pause."""
import pytest

from tests.test_external_gate_dispatch import _blocked
from tests.test_reader_fanout_runtime import _state, _start_fanout
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.workflow_operation import reduce_workflow_operations


@pytest.mark.parametrize("pause_kind", ["dispatch.paused", "run.paused", "run.cancelled"])
def test_cleared_quota_after_restart_cannot_bypass_pause(tmp_path, monkeypatch, pause_kind):
    state, log, transport, runtime = _state(tmp_path)
    runtime.config.workflow.flow_metadata["result_protocol"] = {"mode": "blocking"}
    send = transport.send_task
    def quota(*args, **kwargs):
        raise _blocked("usage_limit_reached")
    monkeypatch.setattr(transport, "send_task", quota)
    runtime.event_writer.emit("run.started", correlation_id="trace-1", payload={"run_id": "trace-1"})
    _start_fanout(runtime, payload={"workflow_run_id": "trace-1"})
    operations = reduce_workflow_operations(log.read_all())
    assert len(operations) == 2
    runtime.event_writer.emit(pause_kind, correlation_id="trace-1", payload={"run_id": "trace-1"})
    monkeypatch.setattr(transport, "send_task", send)
    restarted = Orchestrator(state, runtime.config, transport)
    deferred = []
    for _ in range(2):
        restarted.run_once(events=[])
        deferred.append(len([e for e in log.read_all() if e.type == "fanout.child.dispatch_deferred"]))
    assert not transport.sent
    assert deferred[0] == deferred[1]
    assert not any(e.type == "fanout.child.completed" for e in log.read_all())
    if pause_kind != "run.cancelled":
        restarted.event_writer.emit("loop.resume_requested" if pause_kind == "dispatch.paused" else "run.resumed",
                                   correlation_id="trace-1", payload={"run_id": "trace-1"})
        for _ in range(2):
            restarted.run_once(events=[])
        assert len(transport.sent) == 2
        current = reduce_workflow_operations(log.read_all())
        assert set(current) == set(operations)
        assert all(op["status"] == "running" for op in current.values())
        assert not any(e.type in {"fanout.child.failed", "fanout.child.completed"} for e in log.read_all())
