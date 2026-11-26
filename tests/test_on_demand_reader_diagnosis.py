import json
from threading import Event

import pytest

from tests.test_reader_fanout_runtime import _durable_verification_payload
from tests.test_reader_rebuild_after_transport_retry import exhausted_reader
from tests.test_run_manager_reflection import _wait
from zf.autoresearch.loop_reflect import parse_reflection_response
from zf.core.events import ZfEvent
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_manager import _maybe_invoke_run_manager_reflect, run_manager_tick


@pytest.mark.parametrize("selection", ["bound", "foreign", "pause"])
def test_on_demand_reader_opinion_reaches_one_controlled_repair_across_restart(tmp_path, selection):
    state, log, transport, runtime, config, bound = exhausted_reader(tmp_path)
    config.runtime.run_manager.resident_agent.enabled = False
    config.runtime.run_manager.reflect.enabled = True
    finish = Event()
    case = {"workflow_run_id": bound["workflow_run_id"], "source_event_ids": [bound["source_event_id"]],
            "failure_class": "reader_execution_failure", "action": "diagnose-attention",
            "checkpoint_id": "diag-reader", "owner_route": "run_manager"}

    def diagnose(prompt, **kwargs):
        option = json.loads(prompt.splitlines()[-1])
        assert option["failure_event_id"] == bound["source_event_id"]
        assert option["recommendation"]["action_payload"]["target_operation_id"] == bound["target_operation_id"]
        finish.wait(timeout=5)
        return parse_reflection_response(json.dumps({"verdict": "best_so_far", "risk": "low",
            "rec_for_next_iter": "The failed Reader can acquire its transport again. Rebuild the same child.",
            "selected_reader_recovery": "foreign" if selection == "foreign" else option["option_id"]}))

    assert _maybe_invoke_run_manager_reflect(state_dir=state, writer=runtime.event_writer, config=config,
        project_root=tmp_path, action=case, projection={}, causation_id=bound["source_event_id"], reflect_fn=diagnose)
    assert not log.query(type="repair.action.requested")
    finish.set()
    _wait(lambda: bool(log.query(type="run.manager.reflect.completed")))
    runtime = Orchestrator(state, config, transport)
    if selection == "pause":
        runtime.event_writer.append(ZfEvent(type="run.paused", origin="kernel", actor="operator",
                                            payload={"workflow_run_id": bound["workflow_run_id"]}))
    for _ in range(2):
        run_manager_tick(state_dir=state, writer=runtime.event_writer, config=config,
                         project_root=tmp_path, event_log=log, spawn_repairs=False)
    requests = log.query(type="repair.action.requested")
    before = len(transport.sent)
    for request in requests:
        runtime.run_once(events=[request])
        runtime.run_once(events=[request])
    assert len(transport.sent) - before == int(selection == "bound")
    opinions = [e for e in log.query(type="run.manager.agent.recommendation") if e.payload.get("diagnosis_operation_id")]
    assert len(opinions) == int(selection != "foreign")
    if selection == "bound":
        assert len(requests) == len(log.query(type="repair.action.applied")) == 1
        assert requests[0].payload["source_event_id"] == bound["source_event_id"]
        resumed = log.query(type="fanout.child.dispatched")[-1]
        assert resumed.payload["payload"]["retry_of_operation_id"] == bound["target_operation_id"]
        source = hydrate_sidecar_ref(state, resumed.payload["payload"]["attempt_source_manifest"]).payload
        read_attempt_artifact(state, manifest=source, source_id="context", artifact_id="context", json_path="$.facts")
        completed = runtime.event_writer.append(ZfEvent(
            type="verify.child.completed", actor=resumed.payload["role_instance"],
            task_id="T-VERIFY", correlation_id=bound["workflow_run_id"],
            payload=_durable_verification_payload(resumed.payload, state_dir=state, verdict="passed"),
        ))
        runtime.run_once(events=[completed])
        run_manager_tick(state_dir=state, writer=runtime.event_writer, config=config,
                         project_root=tmp_path, event_log=log, spawn_repairs=False)
        assert log.query(type="workflow.call.result.admitted")
        assert log.query(type="review.approved")
        assert len(log.query(type="run.manager.action.effect.passed")) == 1


def test_identical_checkpoint_names_do_not_share_reflection_across_incidents():
    from zf.runtime.run_manager import _run_manager_reflect_request_id

    action = {"checkpoint_id": "reader", "workflow_run_id": "run-a", "source_event_ids": ["f1"]}
    first = _run_manager_reflect_request_id(action, [])
    assert _run_manager_reflect_request_id({**action, "workflow_run_id": "run-b"}, []) != first
    assert _run_manager_reflect_request_id({**action, "source_event_ids": ["f2"]}, []) != first
    legacy = _run_manager_reflect_request_id({"checkpoint_id": "reader"}, [])
    receipt = ZfEvent(type="run.manager.reflect.requested", origin="kernel",
                      payload={**action, "request_id": legacy})
    assert _run_manager_reflect_request_id(action, [receipt]) == legacy
