"""Recovery successors consume a current receipt, not an action-name exception."""

import pytest

from tests.test_recovery_case import _action, _event
from zf.core.events import EventLog, EventWriter
from zf.runtime.recovery_case import build_recovery_case_projection, recovery_action_admission
from zf.runtime.run_manager import _action_payload, _settle_pending_action_effects


def _handoff():
    pending = _event(
        "run.manager.action.effect.pending", "case", action="diagnose-attention",
        checkpoint_id="diagnosis", request_id="request", target_commit="a" * 40,
        continuation_expected_events=["orchestrator.rework.triage.recorded"],
    )
    result = _event(
        "orchestrator.rework.triage.recorded", "case", request_id="request",
        target_commit="a" * 40,
    )
    action = _action(
        recovery_case_id="case", action="repair-closeout-validate",
        checkpoint_id="repair", request_id="request", recorded_event_id=result.id,
    )
    return [pending, result], action


def test_successor_consumes_receipt_once_without_action_name_exception():
    events, action = _handoff()
    assert recovery_action_admission(events, action)["status"] == "admitted"
    events.append(_event("run.manager.action.planned", "case", **{
        key: value for key, value in _action_payload(action).items() if key != "recovery_case_id"
    }))
    assert recovery_action_admission(events, action)["status"] == "blocked"


@pytest.mark.parametrize("field,value", [
    ("task_map_generation", "stale"), ("target_commit", "b" * 40),
    ("request_id", "other"), ("task_id", "other"),
    ("workflow_run_id", "other"), ("recorded_event_id", "missing"),
])
def test_successor_must_match_current_step_identity(field, value):
    events, action = _handoff()
    action[field] = value
    assert recovery_action_admission(events, action)["status"] == "blocked"


@pytest.mark.parametrize("event_type", ["task_map.ready", "dev.build.done", "fanout.child.completed", "autoresearch.loop.completed"])
def test_progress_is_not_fault_effect(event_type):
    events = [_event("run.manager.action.applied", "case"), _event(event_type, "case")]
    assert build_recovery_case_projection(events)["cases"][0]["status"] != "resolved"


def test_action_payload_preserves_effect_identity():
    action = _action(contract_revision="contract-2", active_attempt_id="attempt-3")
    payload = _action_payload(action)
    for field in ("target_commit", "contract_revision", "active_attempt_id"):
        assert payload[field] == action[field]


@pytest.mark.parametrize("bindings", [{}, {"target_commit": "b" * 40}])
def test_effect_with_declared_binding_rejects_missing_or_stale_evidence(tmp_path, bindings):
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    log.append(_event(
        "run.manager.action.effect.pending", "case", effect_id="effect",
        expected_event_types=["verify.passed"], effect_bindings={"target_commit": "a" * 40},
    ))
    log.append(_event("verify.passed", "case", **bindings))
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    log.append(_event("verify.passed", "case", target_commit="a" * 40))
    assert _settle_pending_action_effects(log.read_all(), writer) == 1
    assert _settle_pending_action_effects(log.read_all(), writer) == 0


def test_resident_recommendation_inherits_request_scope_and_allows_one_successor():
    from zf.runtime.recovery_step import bind_recommendation_to_request
    from zf.runtime.run_manager import _action_from_agent_recommendation
    pending = _event("run.manager.action.effect.pending", "case", request_id="req", checkpoint_id="diagnose",
        continuation_expected_events=["run.manager.agent.recommendation"], target_commit="a" * 40)
    delivery = _event("run.manager.resident.prompted", "case", request_id="req", instance_id="run-manager")
    result = _event("run.manager.agent.recommendation", "case", request_id="req",
        recommended_route="controlled_action", action="repair-closeout-validate")
    events = [pending, delivery, result]
    payload = bind_recommendation_to_request(events, result, result.payload)
    action = _action_from_agent_recommendation(result, payload)
    assert action["target_commit"] == "a" * 40
    assert recovery_action_admission(events, action)["status"] == "admitted"
    with pytest.raises(ValueError, match="binding_mismatch"):
        bind_recommendation_to_request(events, result, {**result.payload, "target_commit": "b" * 40})
    result.actor = "impl-1"
    with pytest.raises(ValueError, match="responder"):
        bind_recommendation_to_request(events, result, result.payload)


def test_effect_timeout_excludes_explicit_pause_duration(tmp_path):
    from datetime import datetime, timedelta, timezone
    from zf.core.events import ZfEvent
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    pending = _event("run.manager.action.effect.pending", "case", effect_id="effect",
        effect_timeout_seconds=60, expected_event_types=["verify.passed"])
    pending.ts = start.isoformat()
    writer.append(pending)
    writer.append(ZfEvent(type="dispatch.paused", ts=(start + timedelta(seconds=10)).isoformat()))
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
