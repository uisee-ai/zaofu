"""A transport retry keeps its durable call identity through controlled repair."""
from copy import deepcopy
from dataclasses import replace

import pytest

from tests.test_reader_fanout_runtime import _durable_reader_state
from tests.test_run_manager_reader_rebuild import consume
from zf.core.events import ZfEvent
from zf.runtime.reader_rebuild_admission import reader_rebuild_error
from zf.runtime.workflow_operation import reduce_workflow_operations


def exhausted_reader(tmp_path):
    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path)
    runtime.event_writer.emit("run.started", payload={"workflow_run_id": "run-durable"})
    for index in range(2):
        closed = runtime.event_writer.emit("provider.turn.closed", actor="verify-1",
            payload={"backend": "codex", "turn_id": f"turn-{index}", "provider_status": "completed"})
        runtime.run_once(events=[closed])
    events = log.read_all()
    failed = next(e for e in reversed(events) if e.type == "fanout.child.failed")
    operation_id = child["payload"]["operation_id"]
    operation = reduce_workflow_operations(events)[operation_id]
    assert operation["status"] == "failed"
    assert failed.payload["run_id"] != operation["active_attempt_id"]
    payload = {"fanout_id": failed.payload["fanout_id"], "fanout_child_id": "verify-1",
               "workflow_run_id": "run-durable", "target_operation_id": operation_id,
               "active_attempt_id": operation["active_attempt_id"], "source_event_id": failed.id}
    return state, log, transport, runtime, config, payload


@pytest.mark.parametrize("restart", [False, True])
def test_rm_repairs_exhausted_transport_retry_without_rewriting_failed_call(tmp_path, restart):
    from zf.runtime.orchestrator import Orchestrator

    state, log, transport, runtime, config, payload = exhausted_reader(tmp_path)
    if restart:
        runtime = Orchestrator(state, config, transport)
    assert reader_rebuild_error(state, log.read_all(), payload) == ""
    old = deepcopy(reduce_workflow_operations(log.read_all())[payload["target_operation_id"]])
    before = len(transport.sent)
    runtime.event_writer.append(ZfEvent(type="run.manager.agent.recommendation", actor="run-manager",
        payload={"recommended_route": "controlled_action", "action": "child-rebuild",
                 "summary": "environment repaired; retry this failed reader", "action_payload": payload}))
    consume(runtime)
    intents = [e for e in log.read_all() if e.type == "repair.action.requested"]
    assert len(intents) == 1
    runtime.run_once(events=intents)
    runtime.run_once(events=intents)
    assert len(transport.sent) == before + 1
    events = log.read_all()
    latest = [e for e in events if e.type == "fanout.child.dispatched"][-1]
    assert latest.payload["operation_id"] != old["operation_id"]
    assert latest.payload["payload"]["retry_of_operation_id"] == old["operation_id"]
    assert reduce_workflow_operations(events)[old["operation_id"]] == old
    assert sum(e.type == "repair.action.applied" for e in events) == 1


@pytest.mark.parametrize("fault", ["attempt", "request", "operation", "run", "role", "origin", "failure_dispatch"])
def test_rebuild_requires_current_transport_and_durable_call_bindings(tmp_path, fault):
    state, log, _, _, _, payload = exhausted_reader(tmp_path)
    events = log.read_all()
    index = max(i for i, e in enumerate(events) if e.type == "fanout.child.dispatched")
    dispatch = events[index]
    data = deepcopy(dispatch.payload)
    if fault == "failure_dispatch":
        index = next(i for i, e in enumerate(events) if e.id == payload["source_event_id"])
        events[index] = replace(events[index], payload={**events[index].payload, "run_id": "old-dispatch"})
    elif fault == "origin":
        events[index] = replace(dispatch, origin="agent")
    else:
        if fault in {"attempt", "request", "operation"}:
            key = {"attempt": "attempt_id", "request": "request_hash", "operation": "operation_id"}[fault]
            data["payload"][key] = "foreign"
        else:
            data[{"run": "workflow_run_id", "role": "role_instance"}[fault]] = "foreign"
        events[index] = replace(dispatch, payload=data)
    assert reader_rebuild_error(state, events, payload)
