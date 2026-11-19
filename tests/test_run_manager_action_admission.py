"""Actual RM recommendation entry must obey the same current recovery gates."""
from unittest.mock import patch

import pytest

from tests.test_run_manager import _config, _state
from zf.runtime.run_manager import run_manager_tick


@pytest.mark.parametrize("scope,expected", [(None, "task"), ("task", "task"), ("run", "run"),
                                          ("side", "side"), ("invalid", "run")])
def test_escalation_producer_preserves_scope_through_admission(tmp_path, scope, expected):
    from zf.runtime.run_manager import emit_human_escalation_package
    state, log, writer = _state(tmp_path)
    action = {"task_id": "T1", "run_id": "run-1", "checkpoint_id": "gate"}
    if scope is not None:
        action["blocking_scope"] = scope
    event = emit_human_escalation_package(writer, action=action, reason="owner input")
    assert event.payload["blocking_scope"] == expected
    _recommend(writer)
    with patch("zf.runtime.run_manager._execute_resident_agent_reprompt", return_value="applied") as effect:
        run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False)
    assert effect.call_count == int(expected != "run")


@pytest.mark.parametrize("route", ["human", "reflect", "repair", "autoresearch"])
def test_stale_recommendation_is_retired_once_without_effect(tmp_path, route):
    state, log, writer = _state(tmp_path)
    _recommend(writer, recommended_route=route, operation_precondition={"generation": "outdated"})
    for _ in range(2):
        run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False)
    assert not any(e.type == "human.escalate" for e in log.read_all())
    receipts = [e for e in log.read_all() if e.type == "run.manager.agent.recommendation.consumed"]
    assert len(receipts) == 1 and receipts[0].payload["status"] == "superseded"


@pytest.mark.parametrize("route", ["human", "reflect", "repair", "autoresearch"])
def test_diagnosis_cannot_recover_a_superseded_attempt(tmp_path, route):
    state, log, writer = _state(tmp_path)
    _recommend(writer, recommended_route=route, task_id="T1",
               workflow_run_id="r", target_operation_id="op-old", active_attempt_id="a-old")
    writer.emit("workflow.operation.requested", payload={"operation_id": "op-new",
        "workflow_run_id": "r", "task_id": "T1", "task_pipeline_stage": "impl",
        "operation_generation": 2, "active_attempt_id": "a-new"})
    for _ in range(2):
        run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False)
    assert not any(e.type == "human.escalate" for e in log.read_all())
    receipts = [e for e in log.read_all() if e.type == "run.manager.agent.recommendation.consumed"]
    assert len(receipts) == 1 and receipts[0].payload["status"] == "superseded"


@pytest.mark.parametrize("successor", [False, True])
def test_current_reader_diagnosis_keeps_owner_escalation(tmp_path, successor):
    state, log, writer = _state(tmp_path)
    identity = {"workflow_run_id": "r", "task_id": "T1",
                "operation_type": "reader", "role_instance": "planner"}
    writer.emit("workflow.operation.requested", payload={**identity, "operation_id": "read-old"})
    writer.emit("workflow.operation.failed", payload={**identity, "operation_id": "read-old"})
    _recommend(writer, recommended_route="human", task_id="T1",
               workflow_run_id="r", target_operation_id="read-old")
    if successor:
        writer.emit("workflow.operation.requested", payload={**identity, "operation_id": "read-new"})
    run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False)
    assert any(e.type == "human.escalate" for e in log.read_all()) == (not successor)


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


@pytest.mark.parametrize("route,consumer", [
    ("autoresearch", "_emit_autoresearch_request"),
    ("reflect", "_maybe_invoke_run_manager_reflect"),
    ("repair", "_emit_repair_acceptance_from_agent"),
])
@pytest.mark.parametrize("blocker", ["active_case", "human_gate", "stale_precondition"])
def test_diagnostic_recommendation_routes_cannot_bypass_common_admission(tmp_path, route, consumer, blocker):
    from zf.runtime.run_manager import _consume_agent_recommendations
    state, log, writer = _state(tmp_path)
    extra = {}
    if blocker == "active_case":
        writer.emit("run.manager.action.applied", payload={"recovery_case_id": "rcase-one"})
        extra["recovery_case_id"] = "rcase-one"
    elif blocker == "human_gate":
        writer.emit("human.escalate", task_id="T2", payload={
            "decision_token": "approval", "blocking_scope": "task", "reason": "owner approval required"})
    else:
        extra["operation_precondition"] = {"generation": "outdated-g1"}
    recommendation = _recommend(writer, recommended_route=route, **extra)
    with patch(f"zf.runtime.run_manager.{consumer}", return_value=True) as effect, \
            patch("zf.runtime.run_manager._run_manager_reflect_enabled", return_value=True), \
            patch("zf.runtime.run_manager._run_manager_source_repair_enabled", return_value=True):
        _consume_agent_recommendations(state_dir=state, writer=writer, config=_config(),
            project_root=tmp_path, events=log.read_all(), causation_id=recommendation.id, reflect_fn=None)
        effect.assert_not_called()
    receipt = [e for e in log.read_all() if e.type == "run.manager.agent.recommendation.consumed"][-1]
    assert receipt.payload["status"] == ("superseded" if blocker == "stale_precondition" else "deferred")


@pytest.mark.parametrize("route,consumer", [
    ("autoresearch", "_emit_autoresearch_request"),
    ("reflect", "_maybe_invoke_run_manager_reflect"),
    ("repair", "_emit_repair_acceptance_from_agent"),
])
def test_current_diagnostic_advice_survives_a_temporary_gate(tmp_path, route, consumer):
    from zf.runtime.run_manager import _consume_agent_recommendations
    state, log, writer = _state(tmp_path)
    recommendation = _recommend(writer, recommended_route=route)
    with patch(f"zf.runtime.run_manager.{consumer}", return_value=True) as effect, \
            patch("zf.runtime.run_manager.action_gate_reason",
                  side_effect=["shared_repair_owned_by_another_case", ""]), \
            patch("zf.runtime.run_manager._run_manager_reflect_enabled", return_value=True), \
            patch("zf.runtime.run_manager._run_manager_source_repair_enabled", return_value=True):
        for _ in range(3):
            _consume_agent_recommendations(state_dir=state, writer=writer, config=_config(),
                project_root=tmp_path, events=log.read_all(),
                causation_id=recommendation.id, reflect_fn=None)
        effect.assert_called_once()
    receipts = [e for e in log.read_all() if e.type == "run.manager.agent.recommendation.consumed"]
    assert [e.payload["status"] for e in receipts] == ["deferred", "consumed"]
