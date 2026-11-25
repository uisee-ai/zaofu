from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.test_goal_idle_driver import _cfg, _goal_started, _state, _Writer
from zf.core.events.model import ZfEvent
from zf.runtime.goal_idle_driver import maybe_emit_goal_idle_rescan


NOW = datetime(2026, 11, 24, 17, 0, tzinfo=timezone.utc)


def _event(kind, payload, *, actor="run-manager", delta=-180, origin="kernel", cause=None):
    return ZfEvent(type=kind, actor=actor, origin=origin, ts=(NOW + timedelta(seconds=delta)).isoformat(),
                   payload=payload, causation_id=cause)


def _records(run="r-1"):
    base = {"workflow_run_id": run, "request_id": "ask", "checkpoint_id": "diagnosis-a",
            "recovery_case_id": "case-a", "action": "resident-agent-reprompt",
            "instance_id": "run-manager", "request_kind": "recovery_diagnosis"}
    reply = ["run.manager.agent.recommendation"]
    receipt = _event("run.manager.resident.prompted", {
        "prompted": True, "instance_id": "run-manager", "request_id": "ask",
        "source_action_checkpoint_id": "diagnosis-a", "expected_output": reply,
        "diagnosis_context": {"workflow_run_id": run, "request_kind": "recovery_diagnosis"},
    })
    verification = _event("run.manager.action.verify.passed", {**base, "status": "passed"})
    pending = _event("run.manager.action.effect.pending",
                     {**base, "effect_id": "effect-a", "expected_event_types": reply, "status": "pending",
                      "effect_timeout_seconds": 1800, "receipt_verification_event_id": verification.id},
                     cause=verification.id)
    return receipt, verification, pending


def _config(max_rescans):
    config = _cfg(idle_ticks=1, max_rescans=max_rescans)
    config.runtime = SimpleNamespace(run_manager=SimpleNamespace(
        resident_agent=SimpleNamespace(enabled=True, instance_id="run-manager", response_timeout_seconds=600)))
    return config


@pytest.mark.parametrize("quota,expected", [(0, "exhausted"), (1, "rescan")])
def test_goal_idle_rechecks_its_original_quota_after_matching_resident_response(quota, expected):
    config, state, writer = _config(quota), _state(), _Writer()
    prompt, receipt, effect = _records()
    events = [_goal_started(), prompt, receipt, effect]
    def tick():
        return maybe_emit_goal_idle_rescan(events, config=config, state=state, event_writer=writer, now=NOW)
    assert tick() == tick() == ""
    assert not writer.appended
    events.append(_event("run.manager.agent.recommendation", {
        "workflow_run_id": "other", "request_id": "ask", "status": "needs_owner_decision"}, origin="worker"))
    assert tick() == ""
    events.append(_event("run.manager.agent.recommendation", {
        "workflow_run_id": "r-1", "request_id": "ask", "status": "needs_owner_decision"}, origin="worker"))
    assert tick() == expected
    assert len(writer.appended) == 1
    assert writer.appended[0].type == ("human.escalate" if quota == 0 else "goal.rescan.requested")


@pytest.mark.parametrize("reason", [
    "foreign_run", "no_receipt", "misbound_prompt", "forged_effect", "resident_expired", "effect_expired",
    "reported_failure", "settled_effect", "new_case_action", "owner_gate", "resident_lost", "resident_dead", "resident_disabled", "no_verified_effect",
])
def test_goal_idle_never_hides_a_stale_or_unowned_request(reason):
    config = _config(0)
    prompt, receipt, effect = _records("other" if reason == "foreign_run" else "r-1")
    if reason == "misbound_prompt":
        prompt.payload["source_action_checkpoint_id"] = "other-checkpoint"
    if reason == "forged_effect":
        effect = replace(effect, origin="worker")
    if reason == "no_verified_effect":
        effect.payload["receipt_verification_event_id"] = "wrong-verification"
    if reason == "effect_expired":
        effect.payload["effect_timeout_seconds"] = 180
    if reason == "resident_expired":
        prompt = replace(prompt, ts=(NOW - timedelta(seconds=600)).isoformat())
    events = [_goal_started(), receipt, effect] if reason == "no_receipt" else [_goal_started(), prompt, receipt, effect]
    if reason in {"reported_failure", "settled_effect", "new_case_action", "owner_gate"}:
        kind = {"reported_failure": "run.manager.action.effect.failed", "settled_effect": "run.manager.action.effect.passed",
                "new_case_action": "run.manager.action.planned", "owner_gate": "run.manager.action.blocked"}[reason]
        events.append(_event(kind, {**effect.payload, "blocker_kind": "human_decision"}))
    if reason == "resident_lost":
        events.append(_event("run.manager.resident.restart_requested", {"instance_id": "run-manager"},
                             actor="run-manager-watchdog"))
    if reason == "resident_dead":
        events.append(_event("run.manager.unhealthy", {"reason": "resident_pane_dead"}, actor="run-manager-watchdog"))
    if reason == "resident_disabled":
        config.runtime.run_manager.resident_agent.enabled = False
    writer = _Writer()
    assert maybe_emit_goal_idle_rescan(events, config=config, state=_state(), event_writer=writer, now=NOW) == "exhausted"
    assert writer.appended[0].type == "human.escalate"


def test_heartbeat_does_not_open_a_second_owner_gate_during_resident_diagnosis(tmp_path):
    from zf.core.config.schema import GoalConfig, ZfConfig
    from zf.core.events import EventLog, EventWriter
    from zf.runtime.tick_services import TickServiceIntervals, TickServiceState, run_standard_tick_services

    directory = tmp_path / "state"
    directory.mkdir()
    log = EventLog(directory / "events.jsonl")
    writer = EventWriter(log, default_origin="kernel")
    config = ZfConfig(goal=GoalConfig(enabled=True, idle_progress_ticks=1, max_rescans=0))
    config.runtime.run_manager.resident_agent.enabled = True
    runtime = SimpleNamespace(state_dir=directory, project_root=tmp_path, config=config, event_log=log, event_writer=writer)
    now = datetime.now(timezone.utc).isoformat()
    writer.append(_goal_started())
    for e in _records():
        writer.append(replace(e, ts=now))
    state = TickServiceState()
    def tick(offset):
        return run_standard_tick_services(runtime, state=state, now=offset,
                                          intervals=TickServiceIntervals(heartbeat_sweep_s=0))
    assert tick(1).heartbeat_sweep
    assert not [e for e in log.read_all() if e.type in {"human.escalate", "goal.rescan.requested"}]
    writer.append(ZfEvent(type="run.manager.agent.recommendation", actor="run-manager", origin="worker",
                          payload={"workflow_run_id": "r-1", "request_id": "ask", "status": "needs_owner_decision"}))
    assert tick(2).heartbeat_sweep
    escalations = [e for e in log.read_all() if e.type == "human.escalate"]
    assert len(escalations) == 1 and escalations[0].payload["rescan_count"] == 0
