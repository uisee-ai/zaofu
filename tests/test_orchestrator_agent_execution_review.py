from dataclasses import replace

import pytest

from tests.test_orchestrator_agent_semantic_failure import _runtime
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_execution_review import pending_execution_reviews
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.run_manager import build_run_manager_projection, _execute_orchestrator_triage_request


def _case(tmp_path, *, failure_class="none", minimal_admitted=False):
    runtime = _runtime(tmp_path)
    policy = runtime.config.workflow.orchestration
    policy.autonomous_actions = ["rework", "continue"]
    policy.review_blocked_tasks = True
    package = next(event.payload for event in runtime.event_log.read_all()
                   if event.type == "plan.artifact_package.admitted")
    context = {"workflow_run_id": "run-semantic-1", "flow_kind": "prd", "task_map_generation": "g1",
               "plan_artifact_package_ref": package["package_ref"],
               "plan_artifact_package_digest": package["package_digest"]}
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
        body = {**identity, "semantic_verdict": "failed", "control_result_ref": result_ref}
        if minimal_admitted and kind == "workflow.call.result.admitted":
            body.pop("active_attempt_id")
        runtime.event_writer.append(ZfEvent(type=kind, task_id="TASK-1", payload={
            **body,
        }))
    return runtime, identity


def _pending(runtime):
    return pending_execution_reviews(state_dir=runtime.state_dir, config=runtime.config,
                                     events=runtime.event_log.read_all())


def test_settled_attempt_survives_cleared_task_dispatch_id(tmp_path):
    from zf.runtime.run_manager import run_manager_tick

    runtime, _ = _case(tmp_path, minimal_admitted=True)
    runtime.task_store.update("TASK-1", active_dispatch_id="")
    result = run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer,
                              config=runtime.config, project_root=runtime.project_root,
                              event_log=runtime.event_log, spawn_repairs=False)
    assert result.actions_applied == 1
    request = next(event for event in runtime.event_log.read_all()
                   if event.type == "orchestrator.semantic.failure.requested")
    assert request.payload["active_attempt_id"] == "lease-task-1"
    prepared = request_semantic_failure_checkpoint(runtime, request)
    assert prepared.context.input_body["checkpoint_context"]["target_attempt_id"] == "lease-task-1"


def test_semantic_review_targets_the_settled_operation_role(tmp_path):
    from tests.test_orchestrator_agent_semantic_failure import _submit_rework
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
    from zf.runtime.run_manager import run_manager_tick
    from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
    from zf.runtime.task_pipeline_rework import derive_impl_rework_requests
    from zf.runtime.workflow_operation import reduce_workflow_operations

    runtime, _ = _case(tmp_path, minimal_admitted=True)
    task = runtime.task_store.get("TASK-1")
    runtime.task_store.update("TASK-1", active_dispatch_id="", contract=replace(
        task.contract, owner_role="dev-b", owner_instance="dev-b",
    ))
    run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer,
                     config=runtime.config, project_root=runtime.project_root,
                     event_log=runtime.event_log, spawn_repairs=False)
    request = next(event for event in runtime.event_log.read_all()
                   if event.type == "orchestrator.semantic.failure.requested")
    prepared = request_semantic_failure_checkpoint(runtime, request)
    assert prepared.context.input_body["checkpoint_context"]["target_role_instance"] == "dev-a"
    assert apply_orchestrator_agent_decision(runtime, _submit_rework(runtime, prepared))["applied"]
    events = runtime.event_log.read_all()
    retries = derive_impl_rework_requests(
        events=events, generation_contexts=task_pipeline_generation_contexts(events),
        operation_rows=reduce_workflow_operations(events).values(),
    )
    assert retries["TASK-1"]["fault"] == "task_pipeline_oa_rework"


def test_rejected_missing_attempt_review_is_reissued_once(tmp_path):
    from zf.runtime.run_manager import run_manager_tick

    runtime, _ = _case(tmp_path, minimal_admitted=True)
    runtime.task_store.update("TASK-1", active_dispatch_id="")
    old = dict(_pending(runtime)[0])
    old.pop("active_attempt_id", None)
    old["request_id"] = old["checkpoint_id"] = "legacy-missing-attempt"
    old["request_revision"] = ""
    _execute_orchestrator_triage_request(writer=runtime.event_writer, action=old,
        causation_id=old["source_event_id"], state_dir=runtime.state_dir, config=runtime.config)
    original = next(e for e in runtime.event_log.read_all()
                    if e.type == "orchestrator.semantic.failure.requested")
    runtime.event_writer.append(ZfEvent(
        type="orchestrator.semantic.checkpoint.rejected", task_id="TASK-1",
        causation_id=original.id,
        payload={"source_event_id": original.id, "reason": "target_attempt_missing"},
    ))
    assert len(_pending(runtime)) == 1
    for _ in range(2):
        run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer,
                         config=runtime.config, project_root=runtime.project_root,
                         event_log=runtime.event_log, spawn_repairs=False)
    requests = [e for e in runtime.event_log.read_all()
                if e.type == "orchestrator.semantic.failure.requested"]
    assert len(requests) == 2
    assert requests[1].payload["active_attempt_id"] == "lease-task-1"
    assert request_semantic_failure_checkpoint(runtime, requests[1]).checkpoint == "semantic_failure"


@pytest.mark.parametrize("reason", ["target_operation_stale", "current_plan_package_missing"])
def test_other_checkpoint_rejections_do_not_replay_the_request(tmp_path, reason):
    runtime, _ = _case(tmp_path, minimal_admitted=True)
    runtime.task_store.update("TASK-1", active_dispatch_id="")
    action = _pending(runtime)[0]
    _execute_orchestrator_triage_request(writer=runtime.event_writer, action=action,
        causation_id=action["source_event_id"], state_dir=runtime.state_dir, config=runtime.config)
    original = next(e for e in runtime.event_log.read_all()
                    if e.type == "orchestrator.semantic.failure.requested")
    runtime.event_writer.append(ZfEvent(
        type="orchestrator.semantic.checkpoint.rejected", task_id="TASK-1",
        causation_id=original.id, payload={"source_event_id": original.id, "reason": reason},
    ))
    assert _pending(runtime) == []


def test_bound_missing_attempt_rejection_does_not_retry_forever(tmp_path):
    runtime, _ = _case(tmp_path, minimal_admitted=True)
    runtime.task_store.update("TASK-1", active_dispatch_id="")
    action = _pending(runtime)[0]
    _execute_orchestrator_triage_request(writer=runtime.event_writer, action=action,
        causation_id=action["source_event_id"], state_dir=runtime.state_dir, config=runtime.config)
    original = next(e for e in runtime.event_log.read_all()
                    if e.type == "orchestrator.semantic.failure.requested")
    runtime.event_writer.append(ZfEvent(
        type="orchestrator.semantic.checkpoint.rejected", task_id="TASK-1",
        causation_id=original.id,
        payload={"source_event_id": original.id, "reason": "target_attempt_missing"},
    ))
    assert _pending(runtime) == []


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


def test_first_settled_impl_failure_is_reviewed_by_run_manager_tick(tmp_path):
    from zf.runtime.run_manager import run_manager_tick

    runtime, _ = _case(tmp_path)
    result = run_manager_tick(
        state_dir=runtime.state_dir,
        writer=runtime.event_writer,
        config=runtime.config,
        project_root=runtime.project_root,
        event_log=runtime.event_log,
        spawn_repairs=False,
    )
    requests = [event for event in runtime.event_log.read_all()
                if event.type == "orchestrator.semantic.failure.requested"]
    assert result.actions_applied == 1
    assert len(requests) == 1
    assert requests[0].payload["target_operation_id"] == "impl-1"
    run_manager_tick(
        state_dir=runtime.state_dir,
        writer=runtime.event_writer,
        config=runtime.config,
        project_root=runtime.project_root,
        event_log=runtime.event_log,
        spawn_repairs=False,
    )
    assert len([event for event in runtime.event_log.read_all()
                if event.type == "orchestrator.semantic.failure.requested"]) == 1


def test_busy_diagnosis_does_not_starve_independent_oa_review(tmp_path):
    from zf.runtime.run_manager import run_manager_tick

    runtime, _ = _case(tmp_path)
    for _ in range(3):
        runtime.event_writer.append(ZfEvent(
            type="run.manager.action.failed", actor="run-manager",
            payload={"checkpoint_id": "other-recovery", "failure_class": "other_failure",
                     "reason": "other recovery made no progress"},
        ))
    runtime.event_writer.append(ZfEvent(
        type="run.manager.autoresearch.requested", actor="run-manager",
        payload={"request_id": "other-diagnosis", "checkpoint_id": "other-checkpoint"},
    ))
    projection = build_run_manager_projection(runtime.state_dir, config=runtime.config,
                                              project_root=runtime.project_root)
    assert projection["pending_actions"][0]["failure_class"] == "no_progress_breaker"
    assert projection["continuation"]["next_operation"]["action"] == "orchestrator-rework-triage"
    assert projection["status_explain"]["next_auto_action"] == "request_orchestrator_triage"
    assert projection["status_explain"]["pending_execution"]["diagnosis_wait"]["reason"] == "autoresearch_inflight"
    assert "other-diagnosis" in projection["status_explain"]["pending_execution"]["diagnosis_wait"]["request_ids"]
    result = run_manager_tick(
        state_dir=runtime.state_dir, writer=runtime.event_writer, config=runtime.config,
        project_root=runtime.project_root, event_log=runtime.event_log, spawn_repairs=False,
    )
    assert result.actions_applied == 1
    assert len([event for event in runtime.event_log.read_all()
                if event.type == "orchestrator.semantic.failure.requested"]) == 1


def test_busy_diagnosis_does_not_bypass_owner_decision(tmp_path):
    runtime, _ = _case(tmp_path)
    for _ in range(3):
        runtime.event_writer.append(ZfEvent(
            type="run.manager.action.failed", actor="run-manager",
            payload={"checkpoint_id": "other-recovery", "failure_class": "other_failure",
                     "reason": "other recovery made no progress"},
        ))
    runtime.event_writer.append(ZfEvent(
        type="run.manager.autoresearch.requested", actor="run-manager",
        payload={"request_id": "other-diagnosis", "checkpoint_id": "other-checkpoint"},
    ))
    runtime.event_writer.append(ZfEvent(
        type="human.escalate", actor="run-manager", correlation_id="run-semantic-1",
        payload={"run_id": "run-semantic-1", "decision_token": "owner-decision",
                 "blocking_scope": "run", "reason": "owner decision required"},
    ))
    projection = build_run_manager_projection(runtime.state_dir, config=runtime.config,
                                              project_root=runtime.project_root)
    assert projection["continuation"]["next_operation"] is None


def test_first_failure_review_can_route_targeted_pipeline_rework(tmp_path):
    from tests.test_orchestrator_agent_semantic_failure import _submit_rework
    from tests.test_task_pipeline_reconciler import _policy, _task
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
    from zf.runtime.run_manager import run_manager_tick
    from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
    from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
    from zf.runtime.task_pipeline_rework import derive_impl_rework_requests
    from zf.runtime.workflow_operation import reduce_workflow_operations

    runtime, _ = _case(tmp_path)
    run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer,
                     config=runtime.config, project_root=runtime.project_root,
                     event_log=runtime.event_log, spawn_repairs=False)
    request = next(event for event in runtime.event_log.read_all()
                   if event.type == "orchestrator.semantic.failure.requested")
    prepared = request_semantic_failure_checkpoint(runtime, request)
    outcome = apply_orchestrator_agent_decision(runtime, _submit_rework(runtime, prepared))
    assert outcome["applied"] is True
    events = runtime.event_log.read_all()
    retries = derive_impl_rework_requests(
        events=events, generation_contexts=task_pipeline_generation_contexts(events),
        operation_rows=reduce_workflow_operations(events).values(),
    )
    assert retries["TASK-1"]["operation_id"] == "impl-1"
    assert retries["TASK-1"]["fault"] == "task_pipeline_oa_rework"
    policy = _policy()
    policy["max_rework_attempts"] = 2
    projection = TaskPipelineReconciler().reconcile(
        policy=policy,
        tasks=[_task("TASK-1", priority=1), _task("DOWNSTREAM", priority=2, blocked_by=["TASK-1"])],
        operations=reduce_workflow_operations(events).values(), attempts=[],
        impl_rework_requests=retries,
    )
    assert [row["task_id"] for row in projection["dispatchable"]["impl"]] == ["TASK-1"]
    assert projection["dispatchable"]["impl"][0]["operation_generation"] == "2"


def test_first_failure_review_includes_latest_same_run_user_goal(tmp_path):
    from zf.runtime.run_manager import run_manager_tick
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    runtime, _ = _case(tmp_path)
    latest = runtime.event_writer.append(ZfEvent(
        type="run.goal.updated", correlation_id="run-semantic-1",
        payload={"run_id": "run-semantic-1", "objective": "Use local test users; preserve final acceptance."},
    ))
    runtime.event_writer.append(ZfEvent(
        type="run.goal.updated", correlation_id="other-run",
        payload={"run_id": "other-run", "objective": "Unrelated project decision."},
    ))
    # Keep the current run as the latest top-level goal while retaining the
    # other-run event in the shared ledger.
    runtime.event_writer.append(ZfEvent(
        type="run.goal.updated", correlation_id="run-semantic-1",
        payload={"run_id": "run-semantic-1", "status": "active"},
    ))
    run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer,
                     config=runtime.config, project_root=runtime.project_root,
                     event_log=runtime.event_log, spawn_repairs=False)
    request = next(event for event in runtime.event_log.read_all()
                   if event.type == "orchestrator.semantic.failure.requested")
    prepared = request_semantic_failure_checkpoint(runtime, request)
    source = next(item for item in prepared.context.source_manifest["sources"]
                  if item["source_id"] == "semantic-failure-input")
    failure_input = hydrate_sidecar_ref(runtime.state_dir, source).payload
    assert failure_input["run_goal"]["objective"] == "Use local test users; preserve final acceptance."
    assert failure_input["run_goal"]["objective_event_id"] == latest.id
    assert "Unrelated project decision" not in str(failure_input)


def test_changed_user_goal_requeues_a_rejected_review_once(tmp_path):
    from tests.test_orchestrator_agent_semantic_failure import _submit_rework
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
    from zf.runtime.run_manager import run_manager_tick

    runtime, _ = _case(tmp_path)
    def tick():
        return run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer,
                                config=runtime.config, project_root=runtime.project_root,
                                event_log=runtime.event_log, spawn_repairs=False)

    tick()
    request = next(event for event in runtime.event_log.read_all()
                   if event.type == "orchestrator.semantic.failure.requested")
    prepared = request_semantic_failure_checkpoint(runtime, request)
    decision = _submit_rework(runtime, prepared)
    runtime.event_writer.append(ZfEvent(
        type="run.goal.updated", correlation_id="run-semantic-1",
        payload={"run_id": "run-semantic-1", "objective": "Updated user decision"},
    ))
    assert apply_orchestrator_agent_decision(runtime, decision)["reason"] == "execution_goal_changed"
    assert len(_pending(runtime)) == 1
    tick()
    tick()
    requests = [event for event in runtime.event_log.read_all()
                if event.type == "orchestrator.semantic.failure.requested"]
    assert len(requests) == 2
    assert requests[0].payload["request_id"] != requests[1].payload["request_id"]


def test_goal_change_while_building_checkpoint_cannot_mix_snapshots(tmp_path, monkeypatch):
    from tests.test_orchestrator_agent_semantic_failure import _submit_rework
    from zf.runtime import orchestrator_agent_semantic_failure as semantic
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
    from zf.runtime.run_manager import run_manager_tick

    runtime, _ = _case(tmp_path)
    run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer,
                     config=runtime.config, project_root=runtime.project_root,
                     event_log=runtime.event_log, spawn_repairs=False)
    request = next(event for event in runtime.event_log.read_all()
                   if event.type == "orchestrator.semantic.failure.requested")
    write = semantic.write_immutable_json_sidecar

    def write_then_change_goal(*args, **kwargs):
        ref = write(*args, **kwargs)
        if kwargs.get("kind") == "semantic_failure_input":
            runtime.event_writer.append(ZfEvent(
                type="run.goal.updated", correlation_id="run-semantic-1",
                payload={"run_id": "run-semantic-1", "objective": "New decision during checkpoint"},
            ))
        return ref

    monkeypatch.setattr(semantic, "write_immutable_json_sidecar", write_then_change_goal)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    decision = _submit_rework(runtime, prepared)
    assert apply_orchestrator_agent_decision(runtime, decision)["reason"] == "execution_goal_changed"


@pytest.mark.parametrize("field", ["target_operation_id", "task_map_generation", "source_event_type"])
def test_first_failure_triage_requires_exact_execution_binding(tmp_path, field):
    from zf.runtime.run_manager_rework_triage import triage_action_preflight

    runtime, _ = _case(tmp_path)
    action = _pending(runtime)[0]
    assert triage_action_preflight(action["action"], action)["status"] == "passed"
    action[field] = ""
    assert triage_action_preflight(action["action"], action)["status"] == "blocked"


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
