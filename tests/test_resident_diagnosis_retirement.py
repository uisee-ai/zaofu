"""An exact completed task retires its old resident diagnosis, not other gates."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.run_manager import _pending_human_decisions, _consume_agent_recommendations
from zf.runtime.quiescent import quiescent_now
from tests.test_run_manager import _config, _state


def scenario(tmp_path):
    state, log, writer = _state(tmp_path)
    writer.default_origin = "kernel"
    identity = {"task_id": "T1", "workflow_run_id": "R1"}
    blocked = writer.emit("dev.blocked", task_id="T1", payload=identity)
    failed = writer.emit("task.attempt.failed", task_id="T1", payload={
        **identity, "source_event_id": blocked.id})
    writer.emit("run.manager.resident.prompted", payload={
        "request_id": "diagnosis-1", "instance_id": "run-manager"})
    pending = writer.emit("run.manager.action.effect.pending", payload={
        **identity, "request_id": "diagnosis-1", "source_event_id": blocked.id,
        "continuation_expected_events": ["run.manager.agent.recommendation"]})
    recommendation = writer.emit("run.manager.agent.recommendation", actor="run-manager", payload={
        **identity, "request_id": "diagnosis-1", "recommended_route": "human"})
    _consume_agent_recommendations(state_dir=state, writer=writer, config=_config(),
        project_root=tmp_path, events=log.read_all(), causation_id=recommendation.id, reflect_fn=None)
    escalation = next(e for e in log.read_all() if e.type == "human.escalate")
    terminal = ZfEvent(type="task.done", task_id="T1", origin="kernel", payload=identity)
    return log.read_all(), failed, pending, recommendation, escalation, terminal


@pytest.mark.parametrize("late_recommendation", [False, True])
def test_actual_consumer_gate_retires_only_completed_diagnosis(tmp_path, late_recommendation):
    events, _, _, recommendation, _, terminal = scenario(tmp_path)
    assert _pending_human_decisions(events)
    if late_recommendation:
        events.insert(events.index(recommendation), terminal)
    else:
        events.append(terminal)
    assert _pending_human_decisions(events) == []
    status = quiescent_now(events, config=SimpleNamespace(goal=SimpleNamespace(
        enabled=True, quiescent_after_escalate=True)), now_epoch=10**12)
    assert not status.quiescent


@pytest.mark.parametrize("change", ["other_task", "other_run", "worker_terminal", "activity",
    "missing_request", "worker_request", "wrong_responder", "wrong_binding", "missing_failure",
    "worker_failure", "unrelated_source", "ordinary_gate", "worker_delivery", "missing_delivery"])
def test_unproven_or_unrelated_facts_do_not_retire_owner_gate(tmp_path, change):
    events, failed, pending, recommendation, escalation, terminal = scenario(tmp_path)
    if change == "other_task": terminal = replace(terminal, task_id="T2", payload={**terminal.payload, "task_id": "T2"})
    elif change == "other_run": terminal = replace(terminal, payload={**terminal.payload, "workflow_run_id": "R2"})
    elif change == "worker_terminal": terminal = replace(terminal, origin="worker")
    elif change == "activity": terminal = replace(terminal, type="worker.heartbeat")
    elif change == "missing_request": events.remove(pending)
    elif change == "worker_request": events[events.index(pending)] = replace(pending, origin="worker")
    elif change == "wrong_responder": events[events.index(recommendation)] = replace(recommendation, actor="unrelated")
    elif change == "wrong_binding": events[events.index(recommendation)] = replace(recommendation, payload={**recommendation.payload, "workflow_run_id": "R2"})
    elif change == "missing_failure": events.remove(failed)
    elif change == "worker_failure": events[events.index(failed)] = replace(failed, origin="worker")
    elif change == "unrelated_source": events[events.index(pending)] = replace(pending, payload={**pending.payload, "source_event_id": "other"})
    elif change == "ordinary_gate": events[events.index(escalation)] = replace(escalation, payload={**escalation.payload, "failure_class": "owner_security_decision"})
    elif change in {"worker_delivery", "missing_delivery"}:
        delivery = next(e for e in events if e.type == "run.manager.resident.prompted")
        if change == "missing_delivery": events.remove(delivery)
        else: events[events.index(delivery)] = replace(delivery, origin="worker")
    assert _pending_human_decisions([*events, terminal])
