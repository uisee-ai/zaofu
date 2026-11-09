"""Reader supersession must release its aggregate's native worker safely."""

import pytest

from tests.test_reader_fanout_runtime import _task_map_synth_orchestrator
from zf.core.events import ZfEvent
from zf.runtime.workflow_operation import reduce_workflow_operations


def _running(tmp_path, monkeypatch):
    state, log, runtime, fanout_id = _task_map_synth_orchestrator(tmp_path)
    dispatch = next(e for e in log.read_all() if e.type == "fanout.synth.dispatched")
    stopped = []
    alive = {"source-reader": True, "task-map-synth": True}

    def terminate(role):
        stopped.append(role)
        alive[role] = False

    monkeypatch.setattr(runtime.transport, "terminate", terminate, raising=False)
    monkeypatch.setattr(runtime.transport, "is_alive", lambda role: alive[role])
    return state, log, runtime, fanout_id, dispatch, stopped


def _supersede(runtime, fanout_id):
    started = next(e for e in runtime.event_log.read_all()
                   if e.type == "fanout.started" and e.payload["fanout_id"] == fanout_id)
    return runtime.event_writer.append(ZfEvent(
        type="fanout.started", actor="zf-cli", correlation_id=started.correlation_id,
        payload={**started.payload, "fanout_id": "fanout-current",
                 "trigger_event_id": "trigger-current"},
    ))


def test_runtime_sweep_cancels_superseded_synth_and_stops_provider_once(tmp_path, monkeypatch):
    state, log, runtime, fanout, dispatch, stopped = _running(tmp_path, monkeypatch)
    operation_id = dispatch.payload["operation_id"]
    before = reduce_workflow_operations(log.read_all())[operation_id]
    request = state / before["request_ref"]["ref"]
    request_bytes = request.read_bytes()
    _supersede(runtime, fanout)

    runtime.run_once(events=[])
    runtime.run_once(events=[])

    after = reduce_workflow_operations(log.read_all())[operation_id]
    assert after["status"] == "cancelled"
    assert stopped == ["task-map-synth"]
    assert runtime._last_worker_state["task-map-synth"] == "idle"
    assert request.read_bytes() == request_bytes
    assert after["request_hash"] == before["request_hash"]
    assert not any(e.type in {"task_map.ready", "fanout.synth.completed"} for e in log.read_all())


def test_current_synth_keeps_running(tmp_path, monkeypatch):
    _state, log, runtime, _fanout, dispatch, stopped = _running(tmp_path, monkeypatch)
    runtime.run_once(events=[])
    assert reduce_workflow_operations(log.read_all())[dispatch.payload["operation_id"]]["status"] == "running"
    assert stopped == []


@pytest.mark.parametrize("new_dispatch_type", ["fanout.synth.dispatched", "workflow.operation.started"])
def test_stale_closeout_does_not_stop_reused_synth_role(tmp_path, monkeypatch, new_dispatch_type):
    _state, log, runtime, fanout, dispatch, stopped = _running(tmp_path, monkeypatch)
    _supersede(runtime, fanout)
    runtime.event_writer.append(ZfEvent(
        type=new_dispatch_type, actor="zf-cli",
        payload={"role_instance": "task-map-synth", "run_id": "current-synth",
                 "dispatch_id": "current-synth", "active_attempt_id": "current-synth"},
    ))
    runtime._recover_pending_reader_fanout_dispatches(log.read_all())
    assert reduce_workflow_operations(log.read_all())[dispatch.payload["operation_id"]]["status"] == "cancelled"
    assert stopped == []


def test_recovered_synth_after_prior_cancel_still_closes_out(tmp_path, monkeypatch):
    _state, log, runtime, fanout, dispatch, stopped = _running(tmp_path, monkeypatch)
    runtime.event_writer.append(ZfEvent(
        type="fanout.cancelled", actor="zf-cli", payload={"fanout_id": fanout},
    ))
    runtime.event_writer.append(ZfEvent(
        type="fanout.aggregate.started", actor="zf-cli", payload={"fanout_id": fanout},
    ))
    runtime.event_writer.append(ZfEvent(
        type="fanout.synth.dispatched", actor="zf-cli", payload=dispatch.payload,
    ))
    _supersede(runtime, fanout)
    runtime.run_once(events=[])
    assert reduce_workflow_operations(log.read_all())[dispatch.payload["operation_id"]]["status"] == "cancelled"
    assert stopped == ["task-map-synth"]
