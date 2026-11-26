"""A lost diagnosis delivery keeps its evidence owner after a controller restart."""
from zf.core.events.model import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.autoresearch_failure_handoff import pending_autoresearch_followups
from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick

from tests.test_run_manager import _config, _stale_runtime_ts, _state, _write_supervisor_attention


def test_stalled_diagnosis_without_a_consumer_reaches_owner_once_after_restart(tmp_path):
    state, log, writer = _state(tmp_path)
    cfg = _config()
    _write_supervisor_attention(state)
    action = build_run_manager_projection(state, events=log.read_all(), config=cfg)["pending_actions"][0]
    request = writer.append(ZfEvent(
        type="run.manager.autoresearch.requested", origin="kernel", actor="run-manager",
        ts=_stale_runtime_ts(), payload={"request_id": "lost", "task_id": "T1",
            "checkpoint_id": action["checkpoint_id"], "fingerprint": action["fingerprint"]},
    ))
    assert run_manager_tick(state_dir=state, config=cfg, writer=writer, event_log=log,
                            spawn_repairs=False).actions_failed == 1
    failure = next(e for e in log.read_all() if e.type == "run.manager.action.failed")
    assert failure.causation_id == request.id
    followup, = pending_autoresearch_followups(log.read_all())
    assert followup["source_event_ids"] == [request.id, failure.id]
    assert followup["diagnosis"]["status"] == "unknown"
    reopened = EventLog(state / "events.jsonl")
    for _ in range(2):
        run_manager_tick(state_dir=state, config=cfg, writer=EventWriter(reopened),
                         event_log=reopened, spawn_repairs=False)
    escalations = [e for e in reopened.read_all() if e.type == "human.escalate"
                   and e.payload.get("checkpoint_id") == followup["checkpoint_id"]]
    assert len(escalations) == 1
    assert escalations[0].task_id == "T1"
    assert not any(e.type == "task.done" for e in reopened.read_all())
    writer.emit("autoresearch.loop.completed", payload={"loop_request_id": "lost"})
    assert pending_autoresearch_followups(log.read_all()) == []


def test_unbound_timeout_does_not_create_a_recovery_owner(tmp_path):
    _, log, writer = _state(tmp_path)
    request = writer.emit("run.manager.autoresearch.requested", actor="run-manager",
                          payload={"request_id": "other"})
    for actor, cause, origin in [("worker", request.id, "worker"), ("run-manager", "missing", "kernel")]:
        writer.append(ZfEvent(type="run.manager.action.failed", actor=actor, origin=origin,
                              causation_id=cause, payload={"failure_class": "diagnosis_consumer_timeout"}))
    assert pending_autoresearch_followups(log.read_all()) == []


def _reader_case(tmp_path):
    from tests.test_reader_rebuild_after_transport_retry import exhausted_reader

    state, log, _, runtime, _, bound = exhausted_reader(tmp_path)
    writer = runtime.event_writer
    request = writer.append(ZfEvent(type="run.manager.autoresearch.requested", actor="run-manager", origin="kernel",
        payload={"request_id": "reader-diagnosis", "workflow_run_id": bound["workflow_run_id"],
                 "source_event_ids": [bound["source_event_id"]], "recovery_case_id": "reader-case",
                 "checkpoint_id": "reader-check", "fanout_id": bound["fanout_id"]}))
    return state, log, writer, request, bound


def test_empty_loop_completion_retains_the_actionable_reader_on_restart(tmp_path):
    from zf.runtime.on_demand_reader_diagnosis import reader_option

    state, log, writer, request, bound = _reader_case(tmp_path)
    result = writer.emit("autoresearch.loop.completed", payload={"loop_request_id": "reader-diagnosis"})
    writer.emit("run.manager.autoresearch.consumed", actor="run-manager",
                payload={"request_id": "reader-diagnosis", "source_event_id": result.id, "next_route": "no_op"})
    writer.emit("run.completed", payload={"workflow_run_id": "another-run"})
    events = EventLog(state / "events.jsonl").read_all()
    followup, = pending_autoresearch_followups(events)
    assert followup["parent_recovery_case_id"] == "reader-case"
    assert followup["workflow_run_id"] == bound["workflow_run_id"]
    assert followup["source_event_ids"] == [bound["source_event_id"], request.id, result.id]
    assert followup["diagnosis"]["status"] == "unknown"
    option = reader_option(state, events, followup)
    assert option["recommendation"]["action_payload"]["target_operation_id"] == bound["target_operation_id"]
    writer.append(ZfEvent(type="fanout.child.completed", origin="kernel",
        payload={"workflow_run_id": bound["workflow_run_id"], "fanout_id": bound["fanout_id"],
                 "child_id": bound["fanout_child_id"], "run_id": "old-attempt"}))
    assert pending_autoresearch_followups(log.read_all())
    writer.emit("run.completed", payload={"workflow_run_id": bound["workflow_run_id"]})
    assert pending_autoresearch_followups(log.read_all()) == []


def test_bound_owner_wait_keeps_its_consumer_across_duplicate_answers(tmp_path):
    _, log, writer, _, _ = _reader_case(tmp_path)
    writer.emit("autoresearch.loop.completed", payload={"request_id": "reader-diagnosis", "diagnosis": {
        "status": "waiting", "diagnosis_owner": "owner",
        "pending_decisions": [{"decision_token": "owner-1"}], "resume_condition": "answer decision owner-1"}})
    writer.emit("autoresearch.loop.completed", payload={"request_id": "reader-diagnosis"})
    assert not pending_autoresearch_followups(log.read_all())


def test_confirmed_repair_is_bound_to_the_diagnosis_result(tmp_path):
    _, log, writer, request, _ = _reader_case(tmp_path)
    result = writer.emit("autoresearch.loop.completed", payload={"request_id": "reader-diagnosis"})
    body = {"recovery_case_id": request.payload["recovery_case_id"],
            "repair_task_payload": {"task_id": "repair-original", "contract": {"scope": ["file"]}}}
    writer.append(ZfEvent(type="autoresearch.bug_candidate.confirmed", origin="kernel", actor="run-manager",
                          causation_id="unrelated-result", payload=body))
    assert pending_autoresearch_followups(log.read_all())
    writer.append(ZfEvent(type="autoresearch.bug_candidate.confirmed", origin="kernel", actor="run-manager",
                          causation_id=result.id, payload=body))
    assert not pending_autoresearch_followups(log.read_all())


def test_successor_dispatch_owns_the_retry_only_for_the_original_reader(tmp_path):
    from tests.test_run_manager_reader_rebuild import consume
    from tests.test_reader_rebuild_after_transport_retry import exhausted_reader

    _, log, _, runtime, _, bound = exhausted_reader(tmp_path)
    writer = runtime.event_writer
    writer.emit("run.manager.autoresearch.requested", actor="run-manager", payload={
        "request_id": "r", "workflow_run_id": bound["workflow_run_id"], "source_event_ids": [bound["source_event_id"]]})
    writer.emit("autoresearch.loop.completed", payload={"request_id": "r"})
    writer.append(ZfEvent(type="fanout.child.dispatched", origin="kernel", payload={
        "workflow_run_id": bound["workflow_run_id"], "fanout_id": bound["fanout_id"], "child_id": "sibling",
        "operation_id": "next", "payload": {"retry_of_operation_id": bound["target_operation_id"]}}))
    assert pending_autoresearch_followups(log.read_all())
    writer.append(ZfEvent(type="run.manager.agent.recommendation", actor="run-manager",
        payload={"recommended_route": "controlled_action", "action": "child-rebuild",
                 "summary": "retry this failed independent reader", "action_payload": bound}))
    consume(runtime)
    runtime.run_once(events=log.query(type="repair.action.requested"))
    assert log.query(type="repair.action.applied")
    assert not pending_autoresearch_followups(log.read_all())


def test_foreign_terminal_receipts_and_ambiguous_checkpoints_do_not_cancel_timeout(tmp_path):
    from zf.runtime.run_manager import _stalled_diagnosis_request

    _, log, writer = _state(tmp_path)
    for run in ["alpha", "beta"]:
        writer.emit("run.started", payload={"workflow_run_id": run})
        writer.append(ZfEvent(type="run.manager.autoresearch.requested", actor="run-manager", origin="kernel",
            ts=_stale_runtime_ts(), payload={"workflow_run_id": run, "checkpoint_id": "same",
                                            "request_id": "dup", "recovery_case_id": run + "-case"}))
    first, second = log.query(type="run.manager.autoresearch.requested")
    action = {"workflow_run_id": "alpha", "action": "diagnose-attention", "checkpoint_id": "same"}
    writer.emit("run.completed", payload={"workflow_run_id": "beta"})
    foreign = writer.emit("autoresearch.loop.completed", payload={"workflow_run_id": "beta", "request_id": "dup"})
    writer.emit("run.manager.autoresearch.consumed", actor="run-manager", payload={
        "workflow_run_id": "alpha", "source_event_id": foreign.id, "request_id": "dup"})
    writer.emit("autoresearch.loop.accepted", payload={"checkpoint_id": "same"})
    writer.emit("run.manager.action.applied", payload={"checkpoint_id": "same"})
    writer.emit("autoresearch.loop.started", payload={"workflow_run_id": "alpha", "request_id": "other",
                                                      "checkpoint_id": "same"})
    assert _stalled_diagnosis_request(log.read_all(), action).id == first.id
    writer.emit("autoresearch.loop.failed", causation_id=first.id, payload={"request_id": "dup"})
    assert _stalled_diagnosis_request(log.read_all(), action) is None
    followup, = pending_autoresearch_followups(log.read_all())
    assert followup["workflow_run_id"] == "alpha"


def test_cross_run_consumption_uses_bound_results_and_accepts_late_success(tmp_path):
    from zf.runtime.run_manager import _consume_autoresearch_results

    state, log, writer = _state(tmp_path)
    requests = {}
    for run in ["alpha", "beta"]:
        requests[run] = writer.emit("run.manager.autoresearch.requested", actor="run-manager",
            payload={"request_id": "dup", "workflow_run_id": run, "checkpoint_id": "same",
                     "recovery_case_id": run + "-case"})
    writer.emit("autoresearch.loop.completed", payload={"request_id": "dup"})
    writer.emit("autoresearch.loop.completed", causation_id=requests["beta"].id,
                payload={"request_id": "dup", "workflow_run_id": "alpha"})
    beta = writer.emit("autoresearch.loop.completed", payload={"request_id": "dup", "workflow_run_id": "beta"})
    alpha_failed = writer.emit("autoresearch.loop.failed", causation_id=requests["alpha"].id,
                               payload={"request_id": "dup"})
    assert _consume_autoresearch_results(state, log.read_all(), writer) == 2
    late = writer.emit("autoresearch.loop.completed", payload={"request_id": "dup", "workflow_run_id": "alpha"})
    assert _consume_autoresearch_results(state, log.read_all(), writer) == 1
    assert _consume_autoresearch_results(state, log.read_all(), writer) == 0
    received = log.query(type="run.manager.autoresearch.consumed")
    assert [(e.payload["workflow_run_id"], e.payload["request_event_id"], e.payload["source_event_id"])
            for e in received] == [("beta", requests["beta"].id, beta.id),
                                   ("alpha", requests["alpha"].id, alpha_failed.id),
                                   ("alpha", requests["alpha"].id, late.id)]


def test_bound_parent_gate_owns_reader_wait_and_unrelated_run_gate_does_not(tmp_path):
    _, log, writer, request, bound = _reader_case(tmp_path)
    writer.emit("autoresearch.loop.completed", payload={"request_id": "reader-diagnosis"})
    for run in ["other-run", bound["workflow_run_id"]]:
        writer.emit("human.escalate", actor="run-manager", correlation_id=run, payload={"workflow_run_id": run,
            "decision_token": "token-" + run, "blocking_scope": "run", "checkpoint_id": "human-" + run,
            "source_event_ids": [request.id], "question": "Approve repair?"})
        assert bool(pending_autoresearch_followups(log.read_all())) == (run == "other-run")


def test_empty_autoresearch_completion_hands_reader_to_on_demand_tick(tmp_path):
    import json

    from tests.test_reader_rebuild_after_transport_retry import exhausted_reader
    from tests.test_run_manager_reflection import _wait
    from zf.autoresearch.loop_reflect import parse_reflection_response
    from zf.runtime.orchestrator import Orchestrator

    state, log, transport, runtime, config, bound = exhausted_reader(tmp_path)
    config.runtime.run_manager.resident_agent.enabled = False
    config.runtime.run_manager.reflect.enabled = True
    writer = runtime.event_writer
    writer.emit("run.manager.autoresearch.requested", actor="run-manager", payload={
        "request_id": "reader-ar", "recovery_case_id": "original", "checkpoint_id": "original",
        "workflow_run_id": bound["workflow_run_id"], "source_event_ids": [bound["source_event_id"]]})
    writer.emit("autoresearch.loop.completed", payload={"request_id": "reader-ar"})
    followup, = pending_autoresearch_followups(log.read_all())

    def diagnose(prompt, **kwargs):
        option = json.loads(prompt.splitlines()[-1])
        assert option["failure_event_id"] == bound["source_event_id"]
        return parse_reflection_response(json.dumps({"verdict": "best_so_far", "risk": "low",
            "rec_for_next_iter": "Rebuild the independently failed reader after transport recovery.",
            "selected_reader_recovery": option["option_id"]}))

    run_manager_tick(state_dir=state, config=config, event_log=log, writer=writer, project_root=tmp_path,
                     spawn_repairs=False, reflect_fn=diagnose)
    _wait(lambda: bool(log.query(type="run.manager.reflect.completed")))
    runtime = Orchestrator(state, config, transport)
    for _ in range(2):
        run_manager_tick(state_dir=state, config=config, event_log=log, writer=runtime.event_writer,
                         project_root=tmp_path, spawn_repairs=False, reflect_fn=diagnose)
    intents = log.query(type="repair.action.requested")
    assert len(intents) == 1
    assert intents[0].payload["source_event_id"] == bound["source_event_id"]
    assert any(e.payload.get("recovery_case_id") == followup["recovery_case_id"]
               for e in log.query(type="run.manager.agent.recommendation"))
    runtime.run_once(events=intents)
    assert len(log.query(type="repair.action.applied")) == 1
    assert not pending_autoresearch_followups(log.read_all())
