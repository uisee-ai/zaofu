"""An admission failure cannot borrow another Plan's recovery authority."""
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.stage_failure_replan import plan_reader_stage_replan


def _config():
    stage = SimpleNamespace(
        id="prd-plan", attempt_domain="plan", topology="fanout_reader",
        trigger="prd.scan.completed", failure_event="",
        aggregate=SimpleNamespace(failure_event="prd.plan.failed", success_event="task_map.ready"),
    )
    return SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))


def _chain():
    old = ZfEvent(id="old-scan", type="prd.scan.completed", correlation_id="run", payload={
        "workflow_run_id": "run", "task_ids": ["OLD"], "failed_task_ids": ["OLD"],
        "rework_attempt": 2, "plan_admission_incident_id": "old-incident",
        "stage_replan_generation": "old-generation", "operator_authorized": True,
        "operator_authorization_ref": "old-approval", "orchestration_delta_ref": "old-delta",
        "orchestrator_decision_ref": {"ref": "old-oa", "sha256": "a" * 64},
    })
    started = ZfEvent(id="old-fanout", type="fanout.started", correlation_id="run", payload={
        "stage_id": "prd-plan", "fanout_id": "old-fanout", "trigger_event_id": old.id,
    })
    ready = ZfEvent(id="new-ready", type="task_map.ready", correlation_id="run", payload={
        "workflow_run_id": "run", "task_ids": ["NEW"], "target_ref": "new-head",
        "task_map_ref": "new-map.json", "task_map_digest": "b" * 64,
    })
    failure = ZfEvent(id="new-failure", type="prd.plan.failed", correlation_id="run",
                     causation_id=ready.id, payload={
        "workflow_run_id": "run", "stage_id": "prd-plan", "failure_scope": "plan_admission",
        "trigger_event_id": ready.id, "plan_admission_incident_id": "new-incident",
        "reason": "scope has 24 files, max is 12",
    })
    return [old, started, ready, failure]


@pytest.mark.parametrize("source_field", ["trigger_event_id", "causation_id"])
def test_admission_replan_uses_current_ready_without_historical_authority(source_field):
    events = _chain()
    if source_field == "causation_id":
        events[-1].payload.pop("trigger_event_id")
    result, note = plan_reader_stage_replan(_config(), events, events[-1])
    assert result is not None, note
    assert result.payload["task_ids"] == ["NEW"]
    assert result.payload["plan_admission_incident_id"] == "new-incident"
    assert result.payload["task_map_ref"] == "new-map.json"
    assert result.payload["rework_attempt"] == 1
    assert result.payload["rework_of"] == (
        "new-ready" if source_field == "trigger_event_id" else "new-failure"
    )
    for key in ("failed_task_ids", "stage_replan_generation", "operator_authorized",
                "operator_authorization_ref", "orchestrator_decision_ref", "orchestration_delta_ref"):
        assert key not in result.payload


@pytest.mark.parametrize("source", ["missing", "other-run", "wrong-type"])
def test_admission_replan_without_valid_causal_ready_fails_closed(source):
    events = _chain()
    if source == "missing":
        events.pop(2)
    elif source == "other-run":
        events[2].payload["workflow_run_id"] = "other-run"
    else:
        events[2].type = "unrelated.event"
    result, note = plan_reader_stage_replan(_config(), events, events[-1])
    assert result is None
    assert note == "reader_origin_missing"


def test_admission_replan_preserves_authority_explicitly_bound_by_current_ready():
    events = _chain()
    events[2].payload.update({
        "operator_authorized": True, "operator_authorization_ref": "current-approval",
        "orchestrator_decision_ref": {"ref": "current-oa", "sha256": "c" * 64},
        "orchestration_delta_ref": "current-delta",
        "plan_admission_incident_id": "previous-incident",
    })
    result, note = plan_reader_stage_replan(_config(), events, events[-1])
    assert result is not None, note
    assert result.payload["operator_authorization_ref"] == "current-approval"
    assert result.payload["orchestrator_decision_ref"]["ref"] == "current-oa"
    assert result.payload["orchestration_delta_ref"] == "current-delta"
    assert result.payload["plan_admission_incident_id"] == "new-incident"


@pytest.mark.mock_e2e
def test_kernel_admission_recovery_emits_only_current_context_and_replay_is_inert(tmp_path):
    from zf.core.events import EventLog, EventWriter
    from zf.runtime.orchestrator_reactor import EventReactorMixin

    event_log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(event_log)
    events = _chain()
    for event in events:
        writer.append(event)
    runtime = SimpleNamespace(config=_config(), state_dir=tmp_path,
                              event_log=event_log, event_writer=writer)
    action = EventReactorMixin._on_reader_stage_failure_replan(runtime, events[-1])
    assert action.action == "dispatch"
    emitted = event_log.read_all()[-1]
    assert emitted.type == "prd.scan.completed"
    assert emitted.causation_id == events[-1].id
    assert emitted.payload["task_ids"] == ["NEW"]
    assert emitted.payload["plan_admission_incident_id"] == "new-incident"
    assert "orchestrator_decision_ref" not in emitted.payload
    assert "operator_authorized" not in emitted.payload
    count = len(event_log.read_all())
    assert EventReactorMixin._on_reader_stage_failure_replan(runtime, events[-1]) is None
    assert len(event_log.read_all()) == count
