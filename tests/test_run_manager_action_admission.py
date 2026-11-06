"""Actual RM recommendation entry must obey the same current recovery gates."""
from unittest.mock import patch

import pytest

from tests.test_run_manager import _config, _state
from zf.runtime.run_manager import run_manager_tick


def _recommend(writer, *, task_id="T2", **extra):
    return writer.emit("run.manager.agent.recommendation", actor="run-manager", payload={
        "recommended_route": "controlled_action", "safe_resume_action": "resident_agent_reprompt",
        "checkpoint_id": "ck-reprompt", "fingerprint": "resident:request",
        "tmux_session": "zf-test", "instance_id": "run-manager",
        "briefing_path": "/tmp/briefing.md", "task_id": task_id, **extra,
    })


@pytest.mark.parametrize("scope,gate_task,allowed", [("run", "T1", False),
    ("task", "T2", False), ("task", "T1", True), ("unknown", "T1", False)])
def test_agent_recommendation_obeys_scoped_human_gate(tmp_path, scope, gate_task, allowed):
    state, log, writer = _state(tmp_path)
    writer.emit("human.escalate", task_id=gate_task, payload={
        "decision_token": "approval", "blocking_scope": scope,
        "checkpoint_id": "owner-gate", "reason": "owner input required",
    })
    _recommend(writer)
    with patch("zf.runtime.run_manager._execute_resident_agent_reprompt", return_value="applied") as effect:
        run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False)
        assert effect.call_count == int(allowed)
    consumed = [e for e in log.read_all() if e.type == "run.manager.agent.recommendation.consumed"]
    assert consumed[-1].payload["status"] == ("consumed" if allowed else "deferred")


def test_paused_recommendation_is_not_lost_and_resumes_once(tmp_path):
    state, log, writer = _state(tmp_path)
    _recommend(writer)
    writer.emit("dispatch.paused", actor="operator")
    with patch("zf.runtime.run_manager._execute_resident_agent_reprompt", return_value="applied") as effect:
        run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False)
        assert effect.call_count == 0
        assert not any(e.type == "run.manager.agent.recommendation.consumed" for e in log.read_all())
        writer.emit("dispatch.resumed", actor="operator")
        for _ in range(2):
            run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False)
        assert effect.call_count == 1


def test_agent_cannot_start_another_effect_for_active_case(tmp_path):
    state, log, writer = _state(tmp_path)
    writer.emit("run.manager.action.applied", payload={"recovery_case_id": "rcase-one"})
    _recommend(writer, recovery_case_id="rcase-one")
    with patch("zf.runtime.run_manager._execute_resident_agent_reprompt") as effect:
        run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False)
        effect.assert_not_called()
    consumed = [e for e in log.read_all() if e.type == "run.manager.agent.recommendation.consumed"][-1]
    assert consumed.payload["reason"] == "recovery_case:active_effect"


def test_stale_agent_operation_precondition_rejected_before_effect(tmp_path):
    state, _, writer = _state(tmp_path)
    _recommend(writer, operation_precondition={"generation": "outdated-g1"})
    with patch("zf.runtime.run_manager._execute_resident_agent_reprompt") as effect:
        run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False)
        effect.assert_not_called()


def test_unknown_operator_approval_is_rejected_once_with_receipt(tmp_path):
    state, log, writer = _state(tmp_path)
    writer.emit("human.escalation.acknowledged", actor="operator", payload={
        "decision_token": "missing", "decision": "approve_controlled_action",
        "action": "resident-agent-reprompt",
    })
    with patch("zf.runtime.run_manager._execute_resident_agent_reprompt") as effect:
        for _ in range(2):
            run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False)
        effect.assert_not_called()
    receipts = [e for e in log.read_all() if e.type == "run.manager.human_decision.rejected"]
    assert len(receipts) == 1
    assert receipts[0].payload["reason"] == "matching escalation not found"
