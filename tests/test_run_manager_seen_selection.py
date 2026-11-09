"""A visible historical recovery obligation must not starve new actions."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tests.test_run_manager_rework_triage import _config, _state
from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick


def _actions(writer):
    actions = []
    for task_id in ("T1", "T2"):
        failed = writer.emit("dev.blocked", task_id=task_id, payload={"reason": "diagnose"})
        actions.append({
            "action": "diagnose-attention", "task_id": task_id,
            "checkpoint_id": "diagnose-" + task_id, "fingerprint": "failure-" + task_id,
            "safe_resume_action": "diagnose_attention", "failure_class": "unknown",
            "source_event_type": failed.type, "source_event_id": failed.id,
            "source_event_ids": [failed.id], "owner_route": "run_manager",
            "action_policy": "needs_diagnosis",
        })
    writer.emit("run.manager.action.applied", payload={"checkpoint_id": "diagnose-T1"})
    return actions


def test_seen_obligation_remains_visible_but_next_action_is_selected(tmp_path):
    state, log, writer = _state(tmp_path)
    actions = _actions(writer)
    with patch("zf.runtime.failure_diagnosis_routing.pending_failure_diagnoses", return_value=actions):
        result = build_run_manager_projection(state, events=log.read_all(), config=_config())
    assert {a["checkpoint_id"] for a in result["pending_actions"]} >= {"diagnose-T1", "diagnose-T2"}
    assert result["continuation"]["next_operation"]["checkpoint_id"] == "diagnose-T2"


@pytest.mark.parametrize("filtered", [False, True])
def test_tick_dispatches_later_diagnosis_once(tmp_path, filtered):
    state, log, writer = _state(tmp_path)
    actions = _actions(writer)
    with patch("zf.runtime.failure_diagnosis_routing.pending_failure_diagnoses", return_value=actions):
        for _ in range(2):
            run_manager_tick(
                state_dir=state, writer=writer, config=_config(), event_log=log,
                spawn_repairs=False, action_filter={"diagnose-attention"} if filtered else None,
            )
    requests = [e for e in log.read_all() if e.type == "run.manager.autoresearch.requested"]
    assert [e.payload["checkpoint_id"] for e in requests] == ["diagnose-T2"]


@pytest.mark.parametrize("filtered", [False, True])
@pytest.mark.parametrize("blocker", ["only_seen", "human_gate", "paused"])
def test_skip_seen_does_not_bypass_other_gates(tmp_path, blocker, filtered):
    state, log, writer = _state(tmp_path)
    actions = _actions(writer)
    if blocker == "only_seen":
        actions = actions[:1]
    elif blocker == "human_gate":
        writer.emit("human.escalate", task_id="T2", payload={
            "decision_token": "approval-T2", "blocking_scope": "task", "reason": "owner input",
        })
    else:
        writer.emit("dispatch.paused", actor="operator")
    with patch("zf.runtime.failure_diagnosis_routing.pending_failure_diagnoses", return_value=actions):
        result = build_run_manager_projection(state, events=log.read_all(), config=_config())
        if blocker == "only_seen":
            assert result["continuation"]["next_operation"] is None
            assert result["pending_actions"]
        run_manager_tick(
            state_dir=state, writer=writer, config=_config(), event_log=log,
            spawn_repairs=False, action_filter={"diagnose-attention"} if filtered else None,
        )
    assert not [e for e in log.read_all() if e.type == "run.manager.autoresearch.requested"]


def test_stalled_request_keeps_existing_retry_eligibility(tmp_path):
    from zf.core.events.model import ZfEvent

    state, log, writer = _state(tmp_path)
    actions = _actions(writer)[1:]
    log.append(ZfEvent(
        type="run.manager.autoresearch.requested",
        ts=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        payload={"checkpoint_id": "diagnose-T2", "fingerprint": "failure-T2", "request_id": "old-request"},
    ))
    with patch("zf.runtime.failure_diagnosis_routing.pending_failure_diagnoses", return_value=actions):
        result = build_run_manager_projection(state, events=log.read_all(), config=_config())
    assert result["continuation"]["next_operation"]["checkpoint_id"] == "diagnose-T2"
