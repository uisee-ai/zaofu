"""Diagnosed input failures can request an existing advisory OA, not auto-rework."""
import pytest

from tests.test_resident_semantic_handoff import _diagnosed, _recommend, _tick
from zf.runtime.orchestrator_agent_execution_review import pending_execution_reviews


def _legacy(runtime, mode):
    policy = runtime.config.workflow.orchestration
    policy.normal_frontier_owner = "run_manager"
    policy.mode = mode
    policy.checkpoints = ["plan_candidate"] if mode == "semantic_control" else []
    policy.checkpoint_policies = {"plan_candidate": "shadow"} if policy.checkpoints else {}
    policy.review_blocked_tasks = False
    policy.autonomous_actions = None


@pytest.mark.parametrize("mode", ["exception_advisor", "semantic_control"])
@pytest.mark.parametrize("failure_class", ["none", "reader_execution_failure"])
def test_diagnosed_failure_reaches_legacy_advisor_once_without_enabling_execution(tmp_path, mode, failure_class):
    runtime, _ = _diagnosed(tmp_path, failure_class=failure_class)
    _legacy(runtime, mode)
    assert pending_execution_reviews(state_dir=runtime.state_dir, config=runtime.config,
                                     events=runtime.event_log.read_all()) == []
    advice = _recommend(runtime, summary="Review fixed input scope before any controlled retry.")
    _tick(runtime)
    _tick(runtime)
    events = runtime.event_log.read_all()
    requests = [e for e in events if e.type == "orchestrator.rework.triage.requested"]
    assert len(requests) == 1
    request = requests[0]
    assert request.payload["apply_policy"] == "proposal_only"
    assert request.payload["request_id"] == "diagnose-1"
    assert request.payload["target_operation_id"] == "impl-1"
    assert request.payload["active_attempt_id"] == "lease-task-1"
    assert advice.id in request.payload["source_event_ids"]
    assert not any(e.type in {"orchestrator.semantic.failure.requested", "task.rework.requested"}
                   for e in events)
    assert not runtime.config.workflow.orchestration.review_blocked_tasks
    assert runtime.config.workflow.orchestration.autonomous_actions is None


@pytest.mark.parametrize("blocker", ["wrong_operation", "new_operation", "missing_request", "human", "paused", "provider", "no_role"])
def test_legacy_diagnostic_handoff_preserves_currentness_and_owner_gates(tmp_path, blocker):
    runtime, identity = _diagnosed(tmp_path, failure_class="provider_error" if blocker == "provider" else "reader_execution_failure")
    _legacy(runtime, "exception_advisor")
    changes = {}
    if blocker == "wrong_operation":
        changes["target_operation_id"] = "old-op"
    elif blocker == "missing_request":
        changes["request_id"] = "missing"
    elif blocker == "new_operation":
        runtime.event_writer.emit("workflow.operation.requested", task_id="TASK-1", payload={
            **identity, "operation_id": "impl-2", "request_hash": "r2", "operation_generation": 2})
    elif blocker == "human":
        runtime.event_writer.emit("human.escalate", task_id="TASK-1", payload={
            "decision_token": "owner", "blocking_scope": "task", "reason": "owner decision"})
    elif blocker == "paused":
        runtime.event_writer.emit("dispatch.paused", actor="operator")
    elif blocker == "no_role":
        runtime.config.roles = [r for r in runtime.config.roles if r.name != "orchestrator"]
    _recommend(runtime, **changes)
    _tick(runtime)
    assert not any(e.type in {"orchestrator.rework.triage.requested", "orchestrator.semantic.failure.requested"}
                   for e in runtime.event_log.read_all())
