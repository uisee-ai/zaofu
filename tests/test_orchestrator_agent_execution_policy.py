from dataclasses import replace

import pytest

from tests.test_orchestrator_agent_semantic_failure import _runtime, _semantic_request, _submit_rework
from zf.core.config.loader import ConfigError, _build_orchestration_flow_policy
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint


def _bounded(tmp_path, actions=None):
    runtime = _runtime(tmp_path)
    runtime.config.workflow.orchestration.autonomous_actions = ["rework", "continue"] if actions is None else actions
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))
    return runtime, prepared


def test_bounded_rework_applies_without_planner_or_critic_and_replays(tmp_path):
    runtime, prepared = _bounded(tmp_path)
    required = {r["source_id"] for r in prepared.context.read_policy["required_reads"]}
    assert "execution-context" in required and "semantic-failure-input" in required
    assert not any(name.startswith("plan-port-") for name in required)
    event = _submit_rework(runtime, prepared)
    first = apply_orchestrator_agent_decision(runtime, event)
    assert first["applied"]
    assert apply_orchestrator_agent_decision(runtime, event) == first
    events = runtime.event_log.read_all()
    assert len([e for e in events if e.type == "orchestrator.semantic.rework.requested"]) == 1
    assert not any(e.type in {"orchestrator.replan_requested", "prd.plan.requested", "critic.gate.requested"} for e in events)


@pytest.mark.parametrize("actions,action", [([], "rework"), (["continue"], "rework"), (["rework"], "invalidate")])
def test_ungranted_direct_actions_rejected(tmp_path, actions, action):
    runtime, prepared = _bounded(tmp_path, actions)
    result = apply_orchestrator_agent_decision(runtime, _submit_rework(runtime, prepared, action=action))
    assert result["status"] == "rejected"
    assert result["reason"].startswith("execution_action_not_authorized")


def test_contract_mutation_after_review_invalidates_execution(tmp_path):
    runtime, prepared = _bounded(tmp_path)
    event = _submit_rework(runtime, prepared)
    task = runtime.task_store.get("TASK-1")
    runtime.task_store.update("TASK-1", contract=replace(task.contract, scope=["new-scope.py"]))
    outcome = apply_orchestrator_agent_decision(runtime, event)
    assert outcome["reason"] == "execution_target_changed:TASK-1"


def test_policy_revocation_rejects_old_decision(tmp_path):
    runtime, prepared = _bounded(tmp_path)
    event = _submit_rework(runtime, prepared)
    runtime.config.workflow.orchestration.autonomous_actions = []
    assert apply_orchestrator_agent_decision(runtime, event)["reason"] == "execution_authority_changed"


def test_local_replan_remains_available_without_direct_execution_grant(tmp_path):
    runtime, prepared = _bounded(tmp_path, [])
    result = apply_orchestrator_agent_decision(runtime, _submit_rework(runtime, prepared, action="return_to_plan"))
    assert result["applied"]
    assert any(e.type == "orchestrator.replan_requested" for e in runtime.event_log.read_all())
    assert not any(e.type == "task_map.ready" for e in runtime.event_log.read_all())


def test_loader_accepts_bounded_pilot_and_rejects_bad_permissions():
    body = {"mode": "semantic_control", "checkpoints": ["pre_impl", "semantic_failure"],
            "pilot_id": "bounded-test", "autonomous_actions": ["delegate", "rework"],
            "review_blocked_tasks": True}
    path = "workflow.orchestration.flow_policies.prd"
    assert _build_orchestration_flow_policy(body, path=path).review_blocked_tasks
    for mutation in ({"autonomous_actions": ["delete_dependency"]},
                     {"autonomous_actions": ["rework", "rework"]},
                     {"autonomous_actions": None}, {"review_blocked_tasks": "true"}):
        with pytest.raises(ConfigError):
            _build_orchestration_flow_policy({**body, **mutation}, path=path)


def test_full_yaml_loader_preserves_flow_authority(tmp_path):
    from tests.test_orchestrator_agent_contracts import _write_config
    from zf.core.config.loader import load_config

    config = load_config(_write_config(tmp_path, """orchestration:
  flow_policies:
    prd:
      mode: semantic_control
      pilot_id: bounded-pilot
      checkpoints: [pre_impl, semantic_failure]
      autonomous_actions: [delegate, rework]
      review_blocked_tasks: true
kind_routes:
  prd:
    pattern_id: test-route
    default_tier: full
stages:
  - id: test-route
    trigger: test.requested
    topology: fanout_reader
    roles: [reader]"""))
    policy = config.workflow.orchestration.flow_policies["prd"]
    assert policy.autonomous_actions == ["delegate", "rework"]
    assert policy.review_blocked_tasks is True
    assert config.workflow.orchestration.autonomous_actions is None


def test_successor_after_review_invalidates_old_decision(tmp_path):
    from tests.test_orchestrator_agent_execution_review import _case
    from zf.core.events.model import ZfEvent

    runtime, identity = _case(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))
    event = _submit_rework(runtime, prepared)
    runtime.event_writer.append(ZfEvent(type="workflow.operation.requested", task_id="TASK-1",
        payload={**identity, "operation_id": "impl-2", "operation_generation": 2}))
    assert apply_orchestrator_agent_decision(runtime, event)["reason"] == "execution_operation_changed"


@pytest.mark.parametrize("rank", [-1, True, "0"])
def test_invalid_dispatch_rank_is_rejected(tmp_path, rank):
    from tests import test_orchestrator_agent_run_plan as fixture
    from zf.runtime.orchestrator_agent_contracts import OrchestratorAgentContractError, normalize_run_orchestration_plan

    runtime, trigger, loaded, _, _ = fixture._runtime(tmp_path)
    _, prepared = fixture._checkpoint(runtime, trigger, loaded)
    plan = fixture._decision(prepared)["run_plan"]
    plan["delegation"][0]["dispatch_rank"] = rank
    with pytest.raises(OrchestratorAgentContractError, match="dispatch_rank"):
        normalize_run_orchestration_plan(plan)
