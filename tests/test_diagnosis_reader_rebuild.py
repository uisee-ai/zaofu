"""Resident recovery inputs keep transport identity, retry authority and product gates separate."""

import json
from dataclasses import replace

import pytest

from tests.test_reader_rebuild_after_transport_retry import exhausted_reader
from zf.core.events import ZfEvent
from zf.runtime.diagnosis_reader_rebuild import diagnosis_reader_rebuild
from zf.runtime.run_manager import _execute_resident_agent_reprompt


def test_resident_context_binds_failed_child_and_lists_actual_owner_gates(tmp_path):
    state, log, _, runtime, config, bound = exhausted_reader(tmp_path)
    failed = next(e for e in log.read_all() if e.id == bound["source_event_id"])
    writer = runtime.event_writer
    aggregate = writer.append(ZfEvent(type="prd.plan.failed", origin="kernel",
        payload={"workflow_run_id": bound["workflow_run_id"], "status": "failed",
                 "fanout_id": bound["fanout_id"], "failure_class": "reader_execution_failure"}))
    action = {"request_kind": "recovery_diagnosis", "request_id": "inspect-reader",
              "source_event_ids": [aggregate.id], "workflow_run_id": bound["workflow_run_id"]}
    previous = diagnosis_reader_rebuild(state, log.read_all(), action)
    assert previous["blocking_human_decisions"] == []
    assert previous["human_gate_status"] == "clear" and previous["as_of_event_id"] == aggregate.id
    gate = writer.emit("human.escalate", payload={"workflow_run_id": bound["workflow_run_id"],
                       "source": "goal_idle_driver", "reason": "goal idle rescans exhausted"})
    writer.emit("human.escalate", payload={"blocking_scope": "side", "reason": "unrelated review"})
    _execute_resident_agent_reprompt(state_dir=state, writer=writer, config=config,
        project_root=tmp_path, action=action, causation_id=aggregate.id)
    card = json.loads((state / "diagnostics/run-manager/inspect-reader/context.json")
                      .read_text())["reader_rebuild"]
    assert card["binding_preflight"] == "passed" and card["proposal_only"]
    assert card["human_gate_status"] == "blocked"
    assert card["blocking_human_decisions"] == [
        {"event_id": gate.id, "blocking_scope": "run", "checkpoint_id": ""}]
    assert card["recommendation"] == {"recommended_route": "controlled_action", "action": "child-rebuild",
        "action_payload": {**bound, "task_id": failed.task_id or failed.payload["task_id"]}}
    assert card["transport_run_id"] == failed.payload["run_id"] != bound["active_attempt_id"]
    assert not any(e.type == "repair.action.requested" for e in log.read_all())


@pytest.mark.parametrize("gate", [False, True])
def test_resident_proposal_uses_normal_consumer_and_preserves_owner_gate(tmp_path, gate):
    from tests.test_run_manager_reader_rebuild import consume

    state, log, _, runtime, _, bound = exhausted_reader(tmp_path)
    if gate:
        runtime.event_writer.emit("human.escalate", payload={
            "workflow_run_id": bound["workflow_run_id"], "blocking_scope": "run",
            "checkpoint_id": "owner-review", "reason": "owner decision needed"})
    call_failure = next(e for e in log.read_all() if e.type == "workflow.operation.failed"
                        and e.causation_id == bound["source_event_id"])
    card = diagnosis_reader_rebuild(state, log.read_all(), {"request_kind": "recovery_diagnosis",
        "workflow_run_id": bound["workflow_run_id"], "source_event_ids": [call_failure.id]})
    assert bool(card["blocking_human_decisions"]) == gate
    runtime.event_writer.emit("run.manager.agent.recommendation", actor="run-manager",
        payload={**card["recommendation"], "summary": "reader execution issue repaired; retry exact child"})
    consume(runtime)
    intents = [e for e in log.read_all() if e.type == "repair.action.requested"]
    assert len(intents) == (0 if gate else 1)
    if intents:
        assert intents[0].payload["source_event_id"] == bound["source_event_id"]


@pytest.mark.parametrize("fault", ["changed_call", "stale_failure", "product_rejected", "foreign_run",
                                   "unrelated_source", "other_operation"])
def test_no_retry_proposal_for_invalid_or_semantic_failure(tmp_path, fault):
    state, log, _, _, _, bound = exhausted_reader(tmp_path)
    events = log.read_all()
    failure = next(e for e in events if e.id == bound["source_event_id"])
    action = {"request_kind": "recovery_diagnosis", "workflow_run_id": bound["workflow_run_id"],
              "source_event_ids": [failure.id]}
    if fault == "changed_call":
        index = max(i for i, e in enumerate(events) if e.type == "fanout.child.dispatched")
        dispatch = events[index]
        call = {**dispatch.payload["payload"], "request_hash": "foreign"}
        events[index] = replace(dispatch, payload={**dispatch.payload, "payload": call})
    elif fault == "stale_failure":
        events.append(replace(failure, id="newer", type="fanout.child.completed"))
    elif fault == "product_rejected":
        events[events.index(failure)] = replace(failure, payload={
            **failure.payload, "semantic_verdict": "rejected", "failure_kind": "quality"})
    elif fault == "foreign_run":
        action["workflow_run_id"] = "other"
    elif fault == "unrelated_source":
        action["source_event_ids"] = [events[0].id]
    else:
        action["target_operation_id"] = "other"
    card = diagnosis_reader_rebuild(state, events, action)
    assert "recommendation" not in card
    if fault in {"changed_call", "stale_failure"}:
        assert card["binding_preflight"] == "blocked" and card["reason"]

def test_missing_gate_evidence_keeps_diagnosis_available_without_retry_template(tmp_path, monkeypatch):
    from zf.core.task.store import TaskStore

    state, log, _, _, _, bound = exhausted_reader(tmp_path)
    def unavailable(_store):
        raise OSError("task dependency data unavailable")

    monkeypatch.setattr(TaskStore, "list_all", unavailable)
    card = diagnosis_reader_rebuild(state, log.read_all(), {"request_kind": "recovery_diagnosis",
        "workflow_run_id": bound["workflow_run_id"], "source_event_ids": [bound["source_event_id"]]})
    assert card["binding_preflight"] == "passed" and card["human_gate_status"] == "unknown"
    assert "recommendation" not in card


def test_task_attempt_diagnosis_reaches_only_its_abstaining_reader(tmp_path):
    from tests.test_reader_settled_failure_rebuild import failed_reader
    from tests.test_run_manager_reader_rebuild import consume

    state, log, transport, runtime, _, failed, op = failed_reader(
        tmp_path, pending_sibling=True, failure_class="verifier_execution_failure")
    events = log.read_all()
    attempt = next(e for e in events if e.type == "task.attempt.failed" and e.payload["operation_id"] == op["operation_id"])
    assert attempt.payload["attempt_id"] != op["active_attempt_id"]
    assert not any(e.type == "test.failed" for e in events)
    action = {"request_kind": "recovery_diagnosis", "request_id": "diagnose-attempt",
              "workflow_run_id": op["workflow_run_id"], "source_event_ids": [attempt.id]}
    _execute_resident_agent_reprompt(state_dir=state, writer=runtime.event_writer, config=runtime.config,
        project_root=runtime.project_root, action=action, causation_id=attempt.id)
    card = json.loads((state / "diagnostics/run-manager/diagnose-attempt/context.json").read_text())["reader_rebuild"]
    assert card["binding_preflight"] == "passed" and card["proposal_only"] and card["human_gate_status"] == "clear"
    proposal = card["recommendation"]
    assert proposal["action_payload"]["source_event_id"] == failed.id
    assert proposal["action_payload"]["active_attempt_id"] == op["active_attempt_id"]
    assert not any(e.type == "repair.action.requested" for e in log.read_all())
    runtime.event_writer.emit("run.manager.agent.recommendation", actor="run-manager",
        payload={**proposal, "summary": "The frozen evidence is available; re-evaluate only this Reader."})
    consume(runtime)
    intents = [e for e in log.read_all() if e.type == "repair.action.requested"]
    assert len(intents) == 1
    assert intents[0].payload["fanout_child_id"] == "verify-1"
    sent = len(transport.sent)
    runtime.run_once(events=intents)
    runtime.run_once(events=intents)
    assert len(transport.sent) == sent + 1
    latest = next(e for e in reversed(log.read_all()) if e.type == "fanout.child.dispatched")
    assert latest.payload["child_id"] == "verify-1"
    assert latest.payload["payload"]["retry_of_operation_id"] == op["operation_id"]
    assert not any(e.type == "test.passed" for e in log.read_all())


def test_task_attempt_diagnosis_requires_the_same_settled_admitted_abstention(tmp_path):
    from tests.test_reader_settled_failure_rebuild import failed_reader

    state, log, _, _, _, child, op = failed_reader(tmp_path, pending_sibling=True)
    history = log.read_all()
    attempt = next(e for e in history if e.type == "task.attempt.failed" and e.payload["operation_id"] == op["operation_id"])
    card = {"request_kind": "recovery_diagnosis", "workflow_run_id": op["workflow_run_id"],
            "source_event_ids": [attempt.id]}
    source = next(e for e in history if e.id == child.causation_id)
    settlement = next(e for e in history if e.id == source.causation_id)
    admission = next(e for e in history if e.id == source.payload["semantic_submit_admission_event_id"])
    for original, changes, extra in [
        (attempt, {}, {"origin": "worker"}), (attempt, {}, {"causation_id": "another-settlement"}),
        (attempt, {"source_event_id": child.id}, {}), (attempt, {"dispatch_id": "other"}, {}),
        (attempt, {"operation_id": "other"}, {}), (settlement, {"workflow_run_id": "other"}, {}),
        (source, {"output_profile_id": "ordinary-verify"}, {}), (admission, {"semantic_verdict": "rejected"}, {}),
        (child, {"control_result_ref": {"ref": "foreign"}}, {}), (child, {"semantic_verdict": "rejected"}, {}),
    ]:
        mutated = [replace(e, payload={**e.payload, **changes}, **extra) if e.id == original.id else e for e in history]
        assert not diagnosis_reader_rebuild(state, mutated, card), (original.type, changes, extra)
