from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.orchestrator_reactor import EventReactorMixin
from zf.runtime.stage_failure_replan import plan_reader_stage_replan


def _config():
    def stage(name, trigger, failure, domain=""):
        return SimpleNamespace(
            id=name, trigger=trigger, topology="fanout_reader",
            attempt_domain=domain, failure_event=failure,
            aggregate=SimpleNamespace(success_event="", failure_event=failure),
        )

    return SimpleNamespace(workflow=SimpleNamespace(stages=[
        stage("prd-plan", "prd.scan.completed", "prd.plan.failed", "plan"),
        stage("prd-discovery", "flow.discovery.requested", "flow.discovery.failed"),
    ]))


def _event(event_type, **payload):
    return ZfEvent(type=event_type, correlation_id="run-1", payload={
        "workflow_run_id": "run-1", "pdd_id": "PDD-1",
        "task_map_generation": "old-generation", **payload,
    })


def _history():
    origin = _event("flow.discovery.requested", rework_attempt=2)
    repair = _event("orchestrator.replan_requested", rework_of="integration-failure",
                    rework_source="integration.failed", rework_attempt=2)
    trigger = _event("prd.scan.completed", rework_of="integration-failure",
                     rework_source="integration.failed", rework_attempt=2)
    started = _event("fanout.started", fanout_id="new-plan", stage_id="prd-plan",
                     trigger_event_id=trigger.id)
    started.payload.pop("task_map_generation")  # Actual reader dispatch shape.
    failure = _event("flow.discovery.failed", fanout_id="old-discovery",
                     stage_id="prd-discovery", affected_task_ids=["DRIVE"])
    discovery_started = _event(
        "fanout.started", fanout_id="old-discovery", stage_id="prd-discovery",
        trigger_event_id=origin.id,
    )
    return [origin, repair, trigger, started, discovery_started, failure]


def test_old_reader_failure_does_not_exhaust_active_candidate_replan():
    events = _history()
    assert plan_reader_stage_replan(_config(), events, events[-1]) == (
        None, "owned_by_active_candidate_replan",
    )


@pytest.mark.parametrize("change", [
    "other_run", "other_generation", "other_pdd", "no_marker", "not_started",
    "cancelled", "completed", "other_rework", "scoped_other_task", "future_repair",
    "conflicting_started_generation",
])
def test_unrelated_or_terminal_repair_does_not_bypass_cap(change):
    events = _history()
    failure = events[-1]
    if change == "other_run":
        events[1].payload["workflow_run_id"] = "run-2"
    elif change == "other_generation":
        events[1].payload["task_map_generation"] = "new-generation"
    elif change == "other_pdd":
        events[1].payload["pdd_id"] = "PDD-2"
    elif change == "no_marker":
        events.pop(1)
    elif change == "not_started":
        events.pop(3)
    elif change in {"cancelled", "completed"}:
        event_type = "fanout.cancelled" if change == "cancelled" else "fanout.aggregate.completed"
        events.insert(-1, _event(event_type, fanout_id="new-plan"))
    elif change == "other_rework":
        events[2].payload["rework_of"] = "unrelated-failure"
    elif change == "scoped_other_task":
        events[1].payload["failed_task_ids"] = ["OTHER"]
    elif change == "future_repair":
        events = [events[0], failure, *events[1:-1]]
    elif change == "conflicting_started_generation":
        events[3].payload["task_map_generation"] = "different-generation"
    assert plan_reader_stage_replan(_config(), events, failure) == (None, "cap_exhausted")


def test_new_planner_own_failure_keeps_its_retry_cap():
    events = _history()
    failure = events[-1]
    failure.type = "prd.plan.failed"
    failure.payload.update(stage_id="prd-plan", fanout_id="new-plan")
    assert plan_reader_stage_replan(_config(), events, failure) == (None, "cap_exhausted")


def test_runtime_caller_retains_failure_without_terminalizing_run(tmp_path):
    events = _history()
    log = EventLog(tmp_path / "events.jsonl")
    for event in events:
        log.append(event)
    reactor = SimpleNamespace(config=_config(), event_log=log, state_dir=tmp_path,
                              event_writer=EventWriter(log), escalation=Mock(), task_store=Mock())
    result = EventReactorMixin._on_reader_stage_failure_replan(reactor, events[-1])
    assert result is None
    assert [event.id for event in log.read_all()] == [event.id for event in events]
    reactor.escalation.escalate.assert_not_called()
