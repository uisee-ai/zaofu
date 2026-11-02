"""Scoped OA replans preserve the authority that actually admitted the decision."""

from types import SimpleNamespace

import pytest

from tests.test_orchestrator_agent_execution_review import _case
from tests.test_orchestrator_agent_plan_binding import _review, _new_package
from tests.test_orchestrator_agent_semantic_failure import _submit_rework
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.replan_resynth import (
    append_replan_resynth_once,
    build_replan_resynth_event,
    plan_missing_replan_resynth_events,
)


def _replan(tmp_path):
    runtime, _ = _case(tmp_path)
    _new_package(runtime)
    prepared = request_semantic_failure_checkpoint(runtime, _review(runtime))
    outcome = apply_orchestrator_agent_decision(
        runtime, _submit_rework(runtime, prepared, action="return_to_plan"),
    )
    assert outcome["applied"], outcome
    events = runtime.event_log.read_all()
    marker = next(e for e in events if e.type == "orchestrator.replan_requested")
    trigger = next(e for e in events if e.type == "prd.plan.requested")
    return runtime, marker, trigger


def test_return_to_plan_reader_can_consume_retained_task_authority(tmp_path):
    runtime, marker, trigger = _replan(tmp_path)
    for key in ("task_map_generation", "plan_artifact_package_ref", "plan_artifact_package_digest"):
        assert trigger.payload[key] == marker.payload[key]
    refs = build_plan_handoff_input_refs(
        state_dir=runtime.state_dir, project_root=runtime.project_root, payload=trigger.payload,
    )
    assert "plan-rework-decision" in {r["source_id"] for r in refs}


def test_wrong_historical_binding_is_repaired_once_without_replaying_correct_trigger(tmp_path):
    runtime, marker, trigger = _replan(tmp_path)
    events = runtime.event_log.read_all()
    malformed = ZfEvent(type=trigger.type, payload={
        **trigger.payload, "task_map_generation": "g2",
        "plan_artifact_package_ref": "new-package.json", "plan_artifact_package_digest": "new-digest",
    })
    before = events[:next(i for i, e in enumerate(events) if e.id == marker.id) + 1]
    repairs = plan_missing_replan_resynth_events([*before, malformed], config=runtime.config)
    assert len(repairs) == 1
    assert repairs[0].payload["task_map_generation"] == "g1"
    writer = EventWriter(EventLog(tmp_path / "recovery" / "events.jsonl"))
    writer.append(malformed)
    first = append_replan_resynth_once(writer, tmp_path / "recovery", repairs[0])
    assert first.id != malformed.id
    assert append_replan_resynth_once(writer, tmp_path / "recovery", repairs[0]).id == first.id
    assert len(writer.event_log.read_all()) == 2
    assert plan_missing_replan_resynth_events([*before, malformed, first], config=runtime.config) == []


@pytest.mark.parametrize("mutation", [
    {"task_map_generation": "missing"}, {"plan_artifact_package_digest": "wrong"},
    {"plan_artifact_package_ref": "wrong.json"},
])
def test_oa_baseline_cannot_silently_rebind_unadmitted_claims(tmp_path, mutation):
    runtime, marker, _ = _replan(tmp_path)
    plan = SimpleNamespace(**{**marker.payload, **mutation,
        "trace_id": "run-semantic-1", "source_event_id": marker.payload["rework_of"],
        "source_event_type": marker.payload["rework_source"], "attempt": 1})
    with pytest.raises(ValueError, match="pinned admitted package"):
        build_replan_resynth_event(plan=plan, events=runtime.event_log.read_all(), config=runtime.config)
