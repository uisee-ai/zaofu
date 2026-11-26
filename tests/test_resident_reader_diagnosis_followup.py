"""A resident-selected diagnosis preserves the Reader lineage across an inconclusive result."""
import json

import pytest

from tests.test_reader_rebuild_after_transport_retry import exhausted_reader
from tests.test_run_manager_reflection import _wait
from zf.autoresearch.loop_reflect import parse_reflection_response
from zf.core.events import ZfEvent
from zf.runtime.autoresearch_failure_handoff import pending_autoresearch_followups
from zf.runtime.run_manager import _consume_agent_recommendations, _write_run_manager_context_sidecars
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def resident_reply(writer, action, route):
    request = {"request_id": action["request_id"], "checkpoint_id": action["checkpoint_id"],
               "workflow_run_id": action["workflow_run_id"], "source_event_ids": action["source_event_ids"],
               "request_kind": "recovery_diagnosis", "recovery_case_id": action["recovery_case_id"],
               "failure_class": action["failure_class"], "instance_id": "run-manager"}
    writer.append(ZfEvent(type="run.manager.resident.prompted", actor="run-manager", origin="kernel",
        payload={"request_id": request["request_id"], "instance_id": "run-manager",
                 "source_action_checkpoint_id": request["checkpoint_id"], "diagnosis_context": request}))
    writer.append(ZfEvent(type="run.manager.action.effect.pending", actor="run-manager", origin="kernel",
        payload={**request, "action": "resident-agent-reprompt", "effect_id": "effect-" + request["request_id"],
                 "effect_expected_events": ["run.manager.agent.recommendation"],
                 "expected_event_types": ["run.manager.agent.recommendation"],
                 "continuation_expected_events": ["run.manager.agent.recommendation"]}))
    return writer.append(ZfEvent(type="run.manager.agent.recommendation", actor="run-manager", origin="worker",
        payload={"request_id": request["request_id"], "checkpoint_id": request["checkpoint_id"],
                 "recommended_route": route, "summary": "Independently examine the incomplete diagnosis."}))


@pytest.mark.parametrize("decision", ["unknown", "select", "stale", "legacy", "legacy-stale", "legacy-paused", "legacy-new-turn"])
def test_resident_reflection_rechecks_bound_reader_after_its_own_diagnosis_timeout(tmp_path, monkeypatch, decision):
    state, log, _, runtime, config, bound = exhausted_reader(tmp_path)
    writer = runtime.event_writer
    config.runtime.run_manager.resident_agent.enabled = True
    config.runtime.run_manager.reflect.enabled = True
    run = bound["workflow_run_id"]
    aggregate = writer.append(ZfEvent(type="prd.plan.failed", origin="kernel",
        payload={"workflow_run_id": run, "status": "failed", "fanout_id": bound["fanout_id"],
                 "failure_class": "reader_execution_failure"}))
    original = {"request_kind": "recovery_diagnosis", "request_id": "resident-initial",
                "checkpoint_id": "diagnose-planner", "recovery_case_id": "reader-case",
                "failure_class": "prd_plan_failed", "source_event_ids": [aggregate.id], "workflow_run_id": run}
    refs = _write_run_manager_context_sidecars(state_dir=state, request_id=original["request_id"],
        action=original, projection={}, project_root=tmp_path, source_event_id=aggregate.id, config=config)
    card = hydrate_sidecar_ref(state, refs["context_ref"]).payload["reader_rebuild"]
    dispatch = next(e for e in log.read_all() if e.id == card["dispatch_event_id"])
    assert card["attempt_identity"]["transport_task_attempt_id"] == dispatch.payload["attempt_id"]
    assert card["attempt_identity"]["durable_operation_active_attempt_id"] == bound["active_attempt_id"]
    assert card["attempt_identity"]["dispatch_call_attempt_id"] == dispatch.payload["payload"]["attempt_id"]
    assert dispatch.payload["attempt_id"] != bound["active_attempt_id"]
    resident_reply(writer, original, "autoresearch")

    def consume(reflect_fn=None):
        return _consume_agent_recommendations(state_dir=state, writer=writer, config=config,
            project_root=tmp_path, events=log.read_all(), causation_id="rm-tick", reflect_fn=reflect_fn)

    consume()
    ar, = log.query(type="run.manager.autoresearch.requested")
    writer.emit("autoresearch.loop.failed", actor="autoresearch", causation_id=ar.id, payload={
        "request_event_id": ar.id, "request_id": ar.payload["request_id"],
        "workflow_run_id": run, "diagnosis": {"status": "incomplete","failure_class": "diagnosis_timeout"}})
    followup, = pending_autoresearch_followups(log.read_all())
    assert bound["source_event_id"] in followup["source_event_ids"]
    if decision == "stale":
        writer.append(ZfEvent(type="fanout.child.dispatched", origin="kernel", payload={
            "workflow_run_id": run, "fanout_id": bound["fanout_id"], "child_id": bound["fanout_child_id"],
            "operation_id": "successor", "payload": {"retry_of_operation_id": bound["target_operation_id"]}}))
    # Replay a delivery recorded before enrichment: its request only cited AR and the result.
    legacy_sources = [ar.id, log.query(type="autoresearch.loop.failed")[-1].id]
    resident_reply(writer, {**followup, "source_event_ids": legacy_sources, "request_id": "resident-followup"}, "reflect")

    def reflect(prompt, **kwargs):
        if decision == "select":
            option = json.loads(prompt.splitlines()[-1])
            return parse_reflection_response(json.dumps({"verdict": "best_so_far", "risk": "low",
                "rec_for_next_iter": "Retry this Reader using the restored condition.",
                "selected_reader_recovery": option["option_id"]}))
        return parse_reflection_response(json.dumps({"verdict": "unknown", "risk": "low",
            "rec_for_next_iter": "Preserve the unresolved condition."}))

    if decision.startswith("legacy"):
        with monkeypatch.context() as old:
            old.setattr("zf.runtime.autoresearch_failure_handoff.resident_reflection_followup", lambda *args: {})
            consume(reflect)
    else:
        consume(reflect)
    _wait(lambda: bool(log.query(type="run.manager.reflect.completed")))
    reflect_request, = log.query(type="run.manager.reflect.requested")
    record = hydrate_sidecar_ref(state, reflect_request.payload["request_ref"]).payload["request"]
    option = record["metadata"].get("reader_recovery_option")
    if decision == "stale" or decision.startswith("legacy"):
        assert not option
    else:
        assert option["failure_event_id"] == bound["source_event_id"]
        assert option["recommendation"]["action_payload"]["active_attempt_id"] == bound["active_attempt_id"]
        assert option["binding_preflight"] == "passed" and option["human_gate_status"] == "clear"
        assert option["option_id"] in record["prompt"]
    if decision == "legacy-stale":
        writer.append(ZfEvent(type="fanout.child.dispatched", origin="kernel", payload={
            "workflow_run_id": run, "fanout_id": bound["fanout_id"], "child_id": bound["fanout_child_id"],
            "operation_id": "successor", "payload": {"retry_of_operation_id": bound["target_operation_id"]}}))
    if decision == "legacy-paused":
        writer.append(ZfEvent(type="run.paused", origin="kernel", actor="operator", payload={"workflow_run_id": run}))
    if decision == "legacy-new-turn":
        writer.append(ZfEvent(type="human.escalate", actor="run-manager", origin="kernel",
            payload={"workflow_run_id": run, "blocking_scope": "run", "checkpoint_id": "safety",
                     "reason": "Owner decision required", "decision_token": "hold"}))
    from zf.runtime.run_manager import run_manager_tick

    for _ in range(2):
        run_manager_tick(state_dir=state, config=config, event_log=log, writer=writer,
                         project_root=tmp_path, spawn_repairs=False, reflect_fn=reflect)
    scoped_reflections = [e for e in log.query(type="run.manager.reflect.requested")
                          if e.payload.get("checkpoint_id") == reflect_request.payload["checkpoint_id"]]
    assert len(scoped_reflections) == (2 if decision == "legacy" else 1)
    if decision == "legacy":
        amendment = scoped_reflections[-1]
        _wait(lambda: len([e for e in log.query(type="run.manager.reflect.completed")
                          if e.payload.get("request_id") == amendment.payload["request_id"]]) == 1)
        new_metadata = hydrate_sidecar_ref(state, amendment.payload["request_ref"]).payload["request"]["metadata"]
        assert new_metadata["reader_option_amendment"]["predecessor_operation_id"] == reflect_request.payload["operation_id"]
        assert new_metadata["reader_recovery_option"]["failure_event_id"] == bound["source_event_id"]
        assert not record["metadata"].get("reader_recovery_option")
        assert amendment.payload["request_id"] != reflect_request.payload["request_id"]
    assert [e.id for e in log.query(type="run.manager.autoresearch.requested")
            if e.payload["recovery_case_id"] in {original["recovery_case_id"], followup["recovery_case_id"]}] == [ar.id]
    repairs = log.query(type="repair.action.requested")
    assert len(repairs) == int(decision == "select")
    if repairs:
        assert repairs[0].payload["source_event_id"] == bound["source_event_id"]
        runtime.run_once(events=repairs)
        assert len(log.query(type="repair.action.applied")) == 1
