"""A resident diagnosis hands current admitted failures to the configured OA."""
import pytest

from tests.test_orchestrator_agent_execution_review import _case
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.recovery_case import build_recovery_case_projection


def _diagnosed(tmp_path, *, failure_class="none", progress_fence=False):
    runtime, identity = _case(tmp_path, failure_class=failure_class)
    writer = runtime.event_writer
    runtime.config.workflow.orchestration.normal_frontier_owner = "wrc"
    failed = writer.emit("dev.blocked", task_id="TASK-1", payload={
        "workflow_run_id": "run-semantic-1", "operation_id": "impl-1", "reason": "unknown blocker",
    })
    writer.emit("run.manager.action.applied", payload={
        "checkpoint_id": "failure-diagnosis:" + failed.id, "recovery_case_id": "case-1",
    })
    admitted = next(e for e in runtime.event_log.read_all() if e.type == "workflow.call.result.admitted")
    precondition = {}
    if progress_fence:
        from zf.runtime.run_manager import build_run_goal_projection, build_run_continuation_projection
        events = runtime.event_log.read_all()
        goal = build_run_goal_projection(events, run_id="run-semantic-1")
        projection = build_run_continuation_projection(events, goal=goal, pending_actions=[], completion_profile={})
        precondition = {k: projection[k] for k in ("run_id", "generation", "progress_digest")}
    writer.emit("run.manager.action.effect.pending", task_id="TASK-1", payload={
        "request_id": "diagnose-1", "checkpoint_id": "failure-diagnosis:" + failed.id, "recovery_case_id": "case-1",
        "task_id": "TASK-1", "workflow_run_id": "run-semantic-1", "task_map_generation": "g1",
        "active_attempt_id": "lease-task-1", "failure_event_ids": [admitted.id],
        "continuation_expected_events": ["run.manager.agent.recommendation"],
        "expected_event_types": ["task.done"],
        "operation_precondition": precondition,
    })
    writer.emit("run.manager.resident.prompted", payload={
        "request_id": "diagnose-1", "instance_id": "run-manager", "prompted": True,
        "expected_output": ["run.manager.agent.recommendation"],
    })
    return runtime, identity


def test_parallel_task_progress_does_not_discard_current_diagnosis(tmp_path):
    runtime, _ = _diagnosed(tmp_path, progress_fence=True)
    runtime.event_writer.emit("task.done", task_id="OTHER", correlation_id="run-semantic-1",
                              payload={"workflow_run_id": "run-semantic-1"})
    _recommend(runtime)
    _tick(runtime)
    events = runtime.event_log.read_all()
    requests = [e for e in events if e.type == "orchestrator.semantic.failure.requested"]
    assert len(requests) == 1 and requests[0].payload["target_operation_id"] == "impl-1"


@pytest.mark.parametrize('change', ['unrelated_progress', 'new_operation', 'new_goal', 'terminal'])
def test_bound_task_precondition_tracks_its_owner_and_goal(tmp_path, change):
    from zf.runtime.recovery_step import bind_recommendation_to_request
    from zf.runtime.resident_semantic_handoff import bind_resident_semantic_handoff
    from zf.runtime.run_manager import _action_from_agent_recommendation, _operation_precondition_holds
    runtime, identity = _diagnosed(tmp_path, progress_fence=True)
    advice = _recommend(runtime)
    events = runtime.event_log.read_all()
    payload = bind_recommendation_to_request(events, advice, advice.payload)
    payload = bind_resident_semantic_handoff(state_dir=runtime.state_dir, config=runtime.config,
                                           events=events, payload=payload)
    action = _action_from_agent_recommendation(advice, payload)
    assert _operation_precondition_holds(events, action)
    if change == 'new_operation':
        runtime.event_writer.emit('workflow.operation.requested', task_id='TASK-1', payload={
            **identity, 'operation_id': 'impl-2', 'operation_generation': 2})
    elif change == 'new_goal':
        runtime.event_writer.emit('run.goal.updated', correlation_id='run-semantic-1',
            payload={'run_id': 'run-semantic-1', 'objective': 'different objective'})
    elif change == 'terminal':
        runtime.event_writer.emit('run.cancelled', correlation_id='run-semantic-1',
                                  payload={'run_id': 'run-semantic-1'})
    else:
        runtime.event_writer.emit('task.done', task_id='OTHER', correlation_id='run-semantic-1',
                                  payload={'workflow_run_id': 'run-semantic-1'})
    assert _operation_precondition_holds(runtime.event_log.read_all(), action) == (change == 'unrelated_progress')


def _tick(runtime):
    return run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer,
                            config=runtime.config, project_root=runtime.project_root,
                            event_log=runtime.event_log, spawn_repairs=False)


def _recommend(runtime, **changes):
    return runtime.event_writer.emit("run.manager.agent.recommendation", actor="run-manager", payload={
        "request_id": "diagnose-1", "task_id": "TASK-1", "recommended_route": "controlled_action",
        "action": "orchestrator-rework-triage", "target_operation_id": "impl-1",
        "summary": "Product scope needs OA decision; retain original work.", **changes,
    })


def test_tick_hands_bound_diagnosis_to_existing_semantic_checkpoint_once(tmp_path):
    runtime, _ = _diagnosed(tmp_path)
    from zf.runtime.run_manager import _write_recovery_context_for_action
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    before = _write_recovery_context_for_action(
        state_dir=runtime.state_dir, writer=runtime.event_writer,
        action={"task_id": "TASK-1", "request_id": "diagnose-1"}, source_event_id="diagnosis",
    )
    before_payload = hydrate_sidecar_ref(runtime.state_dir, before).payload
    advice = _recommend(runtime)
    _tick(runtime)
    _tick(runtime)
    events = runtime.event_log.read_all()
    requests = [e for e in events if e.type == "orchestrator.semantic.failure.requested"]
    assert len(requests) == 1
    request = requests[0]
    assert hydrate_sidecar_ref(runtime.state_dir, before).payload == before_payload
    after = request.payload["recovery_context_ref"]
    assert hydrate_sidecar_ref(runtime.state_dir, after).payload["request_id"] == "diagnose-1"
    assert after["ref"] != before["ref"]
    assert request.payload["request_id"] == "diagnose-1"
    assert request.payload["recovery_case_id"] == "case-1"
    assert request.payload["target_operation_id"] == "impl-1"
    assert request.payload["active_attempt_id"] == "lease-task-1"
    assert advice.id in request.payload["source_event_ids"]
    prepared = request_semantic_failure_checkpoint(runtime, request)
    assert prepared.context.input_body["checkpoint_context"]["target_attempt_id"] == "lease-task-1"
    assert not any(e.type == "run.manager.autoresearch.requested" for e in events)
    assert next(c for c in build_recovery_case_projection(events)["cases"] if c["recovery_case_id"] == "case-1")["status"] != "resolved"
    from tests.test_orchestrator_agent_semantic_failure import _submit_rework
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
    from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
    from zf.runtime.task_pipeline_rework import derive_impl_rework_requests
    from zf.runtime.workflow_operation import reduce_workflow_operations
    assert apply_orchestrator_agent_decision(runtime, _submit_rework(runtime, prepared))["applied"]
    events = runtime.event_log.read_all()
    retries = derive_impl_rework_requests(events=events,
        generation_contexts=task_pipeline_generation_contexts(events),
        operation_rows=reduce_workflow_operations(events).values())
    assert retries["TASK-1"]["fault"] == "task_pipeline_oa_rework"


@pytest.mark.parametrize("blocker", ["wrong_task", "wrong_operation", "new_operation", "new_goal", "disabled", "paused", "human", "missing_request"])
def test_semantic_handoff_preserves_currentness_and_owner_gates(tmp_path, blocker):
    runtime, identity = _diagnosed(tmp_path)
    changes = {}
    if blocker == "wrong_task":
        changes["task_id"] = "OTHER"
    elif blocker == "wrong_operation":
        changes["target_operation_id"] = "old-op"
    elif blocker == "missing_request":
        changes["request_id"] = "missing"
    elif blocker == "new_operation":
        runtime.event_writer.emit("workflow.operation.requested", task_id="TASK-1", payload={
            **identity, "operation_id": "impl-2", "request_hash": "r2", "operation_generation": 2,
        })
    elif blocker == "new_goal":
        runtime.event_writer.emit("run.goal.updated", correlation_id="run-semantic-1",
            payload={"run_id": "run-semantic-1", "objective": "changed before diagnosis response"})
    elif blocker == "disabled":
        runtime.config.workflow.orchestration.review_blocked_tasks = False
    elif blocker == "paused":
        runtime.event_writer.emit("dispatch.paused", actor="operator")
    else:
        runtime.event_writer.emit("human.escalate", task_id="TASK-1", payload={
            "decision_token": "owner", "blocking_scope": "task", "reason": "owner decision",
        })
    _recommend(runtime, **changes)
    _tick(runtime)
    events = runtime.event_log.read_all()
    assert not any(e.type == "orchestrator.semantic.failure.requested" for e in events)
    assert not any(e.type == "run.manager.autoresearch.requested" for e in events)
