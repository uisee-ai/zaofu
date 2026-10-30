from dataclasses import replace

import pytest

from tests.test_orchestrator_agent_semantic_failure import _runtime
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_execution_review import pending_execution_reviews
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.run_manager import build_run_manager_projection, _execute_orchestrator_triage_request


def _case(tmp_path, *, failure_class="none"):
    runtime = _runtime(tmp_path)
    policy = runtime.config.workflow.orchestration
    policy.autonomous_actions = ["rework", "continue"]
    policy.review_blocked_tasks = True
    context = {"workflow_run_id": "run-semantic-1", "flow_kind": "prd", "task_map_generation": "g1"}
    runtime.event_writer.append(ZfEvent(type="run.goal.started", payload={"run_id": "run-semantic-1"}))
    runtime.event_writer.append(ZfEvent(type="task.pipeline.generation.admitted", payload={
        **context, "schema_version": "task-pipeline-generation.v1", "task_ids": ["TASK-1"],
    }))
    result_ref = write_immutable_json_sidecar(runtime.state_dir,
        {"schema_version": "implementation-result.v1", "verdict": "failed", "failure_class": failure_class},
        root="fixtures/results", kind="implementation_result", schema_version="implementation-result.v1",
        created_by="test")
    identity = {**context, "task_id": "TASK-1", "operation_id": "impl-1",
                "operation_type": "task-stage", "request_hash": "r1",
                "task_pipeline_stage": "impl", "operation_generation": 1,
                "role_instance": "dev-a", "active_attempt_id": "lease-task-1"}
    for kind in ("workflow.operation.requested", "workflow.operation.started",
                 "workflow.call.result.admitted", "workflow.operation.settled"):
        runtime.event_writer.append(ZfEvent(type=kind, task_id="TASK-1", payload={
            **identity, "semantic_verdict": "failed", "control_result_ref": result_ref,
        }))
    return runtime, identity


def _pending(runtime):
    return pending_execution_reviews(state_dir=runtime.state_dir, config=runtime.config,
                                     events=runtime.event_log.read_all())


def test_run_manager_observes_and_requests_one_exact_checkpoint(tmp_path):
    runtime, _ = _case(tmp_path)
    actions = _pending(runtime)
    assert len(actions) == 1
    projection = build_run_manager_projection(runtime.state_dir, config=runtime.config,
                                              project_root=runtime.project_root)
    assert any(row.get("request_id") == actions[0]["request_id"] for row in projection["pending_actions"])
    outcome = _execute_orchestrator_triage_request(writer=runtime.event_writer, action=actions[0],
        causation_id=actions[0]["source_event_id"], state_dir=runtime.state_dir, config=runtime.config)
    assert outcome == "applied"
    request = next(e for e in runtime.event_log.read_all() if e.type == "orchestrator.semantic.failure.requested")
    prepared = request_semantic_failure_checkpoint(runtime, request)
    assert prepared.checkpoint == "semantic_failure"
    assert request.payload["target_operation_id"] == "impl-1"
    assert _pending(runtime) == []
    runtime.event_writer.append(ZfEvent(type="worker.heartbeat", task_id="TASK-1"))
    assert _pending(runtime) == []


def test_review_uses_configured_oa_profile_instance(tmp_path):
    runtime, _ = _case(tmp_path)
    runtime.config.workflow.orchestration.oa_profile = "dev-a"

    assert len(_pending(runtime)) == 1


def test_review_fails_closed_for_missing_oa_profile(tmp_path):
    runtime, _ = _case(tmp_path)
    runtime.config.workflow.orchestration.oa_profile = "missing-oa"

    assert _pending(runtime) == []


@pytest.mark.parametrize("change", ["disabled", "paused", "done", "successor", "settled_verify", "other_generation", "other_run", "passed"])
def test_ordinary_progress_or_out_of_scope_does_not_wake_oa(tmp_path, change):
    runtime, identity = _case(tmp_path)
    if change == "disabled":
        runtime.config.workflow.orchestration.review_blocked_tasks = False
    elif change == "paused":
        runtime.event_writer.append(ZfEvent(type="run.goal.updated", payload={"run_id": "run-semantic-1", "status": "paused"}))
    elif change == "done":
        runtime.task_store.update("TASK-1", status="done")
    elif change == "successor":
        runtime.event_writer.append(ZfEvent(type="workflow.operation.requested", task_id="TASK-1",
            payload={**identity, "operation_id": "impl-2", "operation_generation": 2}))
    elif change == "settled_verify":
        for kind in ("workflow.operation.requested", "workflow.operation.settled"):
            runtime.event_writer.append(ZfEvent(type=kind, task_id="TASK-1", payload={
                **identity, "operation_id": "verify-1", "task_pipeline_stage": "verify",
            }))
    else:
        events = runtime.event_log.read_all()
        key, value = {"other_generation": ("task_map_generation", "old"),
                      "other_run": ("workflow_run_id", "different"), "passed": ("semantic_verdict", "passed")}[change]
        events = [replace(e, payload={**e.payload, key: value}) if e.type.startswith("workflow.") else e for e in events]
        assert pending_execution_reviews(state_dir=runtime.state_dir, config=runtime.config, events=events) == []
        return
    assert _pending(runtime) == []


@pytest.mark.parametrize("failure_class", ["environment", "provider", "transient_transport"])
def test_nonsemantic_fault_stays_with_recovery_owner(tmp_path, failure_class):
    runtime, _ = _case(tmp_path, failure_class=failure_class)
    assert _pending(runtime) == []


def test_old_generation_high_retry_number_does_not_hide_current_impl(tmp_path):
    runtime, identity = _case(tmp_path)
    runtime.event_writer.append(ZfEvent(type="workflow.operation.requested", task_id="TASK-1",
        payload={**identity, "operation_id": "old-impl", "task_map_generation": "old",
                 "operation_generation": 99}))
    assert len(_pending(runtime)) == 1


def test_new_successor_invalidates_already_queued_review(tmp_path):
    from zf.runtime.orchestrator_agent_semantic_failure import SemanticFailureCheckpointError

    runtime, identity = _case(tmp_path)
    action = _pending(runtime)[0]
    _execute_orchestrator_triage_request(writer=runtime.event_writer, action=action,
        causation_id=action["source_event_id"], state_dir=runtime.state_dir, config=runtime.config)
    request = next(e for e in runtime.event_log.read_all() if e.type == "orchestrator.semantic.failure.requested")
    runtime.event_writer.append(ZfEvent(type="workflow.operation.requested", task_id="TASK-1",
        payload={**identity, "operation_id": "impl-2", "operation_generation": 2}))
    with pytest.raises(SemanticFailureCheckpointError, match="target_operation_stale"):
        request_semantic_failure_checkpoint(runtime, request)


def test_planned_action_without_emitted_request_does_not_consume_review(tmp_path):
    runtime, _ = _case(tmp_path)
    action = _pending(runtime)[0]
    runtime.event_writer.append(ZfEvent(type="run_manager.action.planned", task_id="TASK-1",
        payload={"request_id": action["request_id"]}))
    assert _pending(runtime) == [action]
