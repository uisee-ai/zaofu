"""Actual RM recommendation entry must obey the same current recovery gates."""
from unittest.mock import patch

import pytest

from tests.test_run_manager import _config, _state
from zf.runtime.run_manager import run_manager_tick


@pytest.mark.parametrize('invalid', ['', 'untrusted_alert', 'wrong_run', 'missing_failure',
                                  'untrusted_failure', 'product_failure', 'mutation', 'other_gate'])
def test_host_budget_exception_is_bound_to_real_candidate_diagnosis(tmp_path, invalid):
    from zf.core.events import ZfEvent
    from zf.runtime.candidate_infrastructure_recovery import pending_candidate_infrastructure_diagnoses
    from zf.runtime.run_manager import _pending_human_decisions
    from zf.runtime.run_manager_action_admission import action_gate_reason
    from zf.runtime.run_continuation import human_decisions_block_action
    state, log, writer = _state(tmp_path)
    failure = writer.append(ZfEvent(type='test.failed', origin='kernel', correlation_id='run-1',
        payload={'workflow_run_id': 'run-1', 'failure_scope': 'candidate',
                 'failure_kind': 'infra', 'fanout_id': 'f1'}))
    action = pending_candidate_infrastructure_diagnoses(log.read_all())[0]
    alert = writer.append(ZfEvent(type='human.escalate', origin='kernel', actor='run-manager',
        correlation_id='run-1', payload={'blocking_scope': 'verification',
            'reason': 'preauthorized_probe_budget_exhausted', 'host_manifest_digest': 'a' * 64}))
    events = log.read_all()
    if invalid == 'untrusted_alert': events[-1].origin = 'worker'
    if invalid == 'wrong_run': action['workflow_run_id'] = 'other'
    if invalid == 'missing_failure': action['source_event_ids'] = ['absent']
    if invalid == 'untrusted_failure': events[0].origin = 'worker'
    if invalid == 'product_failure': events[0].payload['failure_kind'] = 'quality'
    if invalid == 'mutation': action['action'] = 'workflow-batch-resume'
    if invalid == 'other_gate': events.append(ZfEvent(type='human.escalate', payload={'reason': 'owner approval'}))
    gates = _pending_human_decisions(events)
    assert bool(human_decisions_block_action(gates, action, events=events)) is bool(invalid)
    assert (action_gate_reason(state, events, action, gates) == '') is (not invalid)
    assert alert.id in {e.id for e in events} and failure.id in {e.id for e in events}


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


@pytest.mark.parametrize("scope", ["run", "side"])
@pytest.mark.parametrize("action_field,question_field", [
    ("recommended_replan_or_resume_action", "decision_required"),
    ("recommended_run_manager_action", "question"),
])
def test_resident_review_only_gate_shows_question_and_real_owner_actions(tmp_path, scope, action_field, question_field):
    from zf.runtime.operator_inbox import build_runtime_requests
    from zf.runtime.run_manager import _pending_human_decisions

    state, log, writer = _state(tmp_path)
    writer.emit("run.goal.started", payload={"run_id": "r", "objective": "deliver"})
    question = "是否接受隔离诊断的现有归档，并保留报告缺失记录？"
    _recommend(writer, task_id="", recommended_route="human", safe_resume_action="agent_recommendation",
               workflow_run_id="r", blocking_scope=scope, summary="归档处置",
               **{action_field: {question_field: question}})
    for _ in range(2):
        run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False)
    events = log.read_all()
    escalation, = [e for e in events if e.type == "human.escalate"]
    assert escalation.payload["question"] == question
    gate, = _pending_human_decisions(events)
    assert gate["blocking_scope"] == scope
    last_tick = next(e for e in reversed(events) if e.type == "run.manager.tick.completed")
    if scope == "run":
        assert last_tick.payload["transition"] == "continue_waiting"
        assert last_tick.payload["wait_reason"] == "human_decision_pending"
        assert last_tick.payload["blocking_event_ids"] == [gate["event_id"]]
    else:
        assert "blocking_event_ids" not in last_tick.payload
    assert gate["question"] == question
    assert "dismissed" in gate["suggested_options"]
    assert "approve_controlled_action" not in gate["suggested_options"]
    assert "/actions/human-decision-dismiss " in gate["approval_command"]
    item, = [i for i in build_runtime_requests(state, events) if i["kind"] == "human_decision"]
    assert item["summary"] == question
    assert "human-decision-approve-controlled-action" not in {op["action"] for op in item["actions"]}
    assert not any(e.type == "run.manager.human_decision.applied" for e in events)


def test_legacy_review_gate_recovers_bound_question_without_dismissing_gate(tmp_path):
    from zf.core.events import ZfEvent
    from zf.runtime.operator_inbox import build_runtime_requests
    from zf.runtime.run_manager import _pending_human_decisions

    state, log, writer = _state(tmp_path)
    question = "是否接受现有诊断记录？"
    rec = _recommend(writer, recommended_route="human",
                     recommended_replan_or_resume_action={"decision_required": question})
    log.append(ZfEvent(type="human.escalate", origin="kernel", actor="run-manager", payload={
        "action": "agent-recommendation", "safe_resume_action": "agent_recommendation",
        "source_event_ids": [rec.id], "checkpoint_id": f"recommendation:{rec.id}",
        "decision_token": "test-gate", "blocking_scope": "run", "reason": "generic owner input",
        "question": "default approval question", "suggested_options": ["approve_controlled_action"],
        "approval_command": "POST /api/projects/<id>/actions/human-decision-approve-controlled-action payload={}",
    }))
    events = log.read_all()
    gate, = _pending_human_decisions(events)
    assert gate["question"] == question and gate["blocking_scope"] == "run"
    assert "/actions/human-decision-dismiss " in gate["approval_command"]
    item, = build_runtime_requests(state, events)
    assert item["summary"] == question and item["status"] == "pending"
    assert log.read_all() == events


@pytest.mark.parametrize("fields,expected", [
    ({"question": "direct", "recommended_replan_or_resume_action": {"decision_required": "plan"}}, "direct"),
    ({"recommended_replan_or_resume_action": {"decision_required": "plan"}}, "plan"),
    ({"recommended_run_manager_action": "invalid"}, "summary"),
    ({"recommended_run_manager_action": {"question": []}, "summary": " "}, "reason"),
])
def test_resident_recommendation_question_preserves_precedence(fields, expected):
    from zf.runtime.run_manager_recovery_actions import resident_decision_question
    payload = {"recommended_run_manager_action": {"question": "action question"},
               "summary": "summary", "reason": "reason", **fields}
    assert resident_decision_question(payload) == expected
