import pytest

from tests import test_orchestrator_agent_run_plan as fixture
from tests.test_task_pipeline_reconciler import _policy
from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.oa_task_verify_assignment import VERIFY_WAIT, _request_correction, request_verification_corrections
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_operations import prepared_operation_from_checkpoint_event


def setup_run(tmp_path, monkeypatch, *, settle_impl=True):
    task_id = "task-local"
    old_port = fixture._port

    def port(state_dir, name, **kwargs):
        original = old_port(state_dir, name, **kwargs)
        if name != "task_map":
            return original
        body = {"schema_version": "task_map.v1", "tasks": [{"task_id": task_id}]}
        ref = write_immutable_json_sidecar(state_dir, body, root="fixtures/maps", kind="task_map",
                                           schema_version="task_map.v1", created_by="test")
        return {**original, "ref": ref["ref"], "sha256": ref["sha256"]}

    monkeypatch.setattr(fixture, "_port", port)
    runtime, _, loaded, _, _ = fixture._runtime(tmp_path)
    runtime.config.workflow.orchestration.autonomous_actions = ["delegate"]
    runtime.config.roles.append(RoleConfig(name="reader", instance_id="reader", role_kind="reader", backend="mock"))
    policy = _policy()
    policy.update(mode="blocking", verify_assignment_mode="oa_run_plan")
    policy["pools"]["verify"].update(role_instances=["reader"], capacity=1)
    runtime.config.workflow.flow_metadata["task_pipeline"] = policy
    runtime.task_store = TaskStore(runtime.state_dir / "kanban.json")
    task = Task(id=task_id, title="Local", status="in_progress", active_dispatch_id="physical-p2",
                contract=TaskContract(behavior="compute", acceptance="check the result"))
    runtime.task_store.add(task)
    identity = {**vars(loaded), "schema_version": "task-pipeline-generation.v1", "task_ids": [task_id]}
    def append(kind, payload, *, actor="orchestrator", origin="kernel"):
        return runtime.event_writer.append(ZfEvent(type=kind, actor=actor, origin=origin, task_id=task_id,
            correlation_id=loaded.workflow_run_id, payload={**payload, "workflow_run_id": loaded.workflow_run_id}))
    generation = append("task.pipeline.generation.admitted", identity)
    context = {**identity, "flow_kind": "prd", "generation_admitted_event_id": generation.id}
    op = {"operation_id": "impl-logical", "task_id": task_id, "task_pipeline_stage": "impl",
          "active_attempt_id": "physical-attempt", "request_hash": "r",
          "operation_generation": 2, "task_map_generation": loaded.task_map_generation}
    append("workflow.operation.requested", op)
    if settle_impl:
        append("workflow.call.result.admitted", {**op, "control_result_ref": {"source_event_id": "build-result"}})
        append("workflow.operation.settled", op)
    release = {"task_id": task_id, "operation_id": op["operation_id"], "attempt_id": "physical-attempt",
               "dispatch_id": task.active_dispatch_id, "source_event_id": "build-result", "status": "succeeded"}
    def sweep():
        request_verification_corrections(runtime, {"tasks": [{"task_id": task_id, "blockers": [VERIFY_WAIT]}]},
                                         {task_id: context}, runtime.event_log.read_all())
        return runtime.event_log.query(type="orchestrator.semantic.checkpoint.requested")
    def decide(prepared):
        decision = fixture._decision(prepared, role_ref="reader", source=prepared.context.source_manifest["sources"][0])
        unit = decision["run_plan"]["graph"]["work_units"][0]
        unit.update(stage_ids=["verify"], task_ids=[task_id], verification_scope={
            "acceptance_ids": ["*"], "required_evidence": ["run the local check"]})
        decision["run_plan"]["delegation"][0].update(preferred_role_refs=["reader"], capability_refs=[])
        return apply_orchestrator_agent_decision(runtime, fixture._submit(runtime, prepared, decision))
    return runtime, task, context, generation, append, sweep, decide, release


def test_new_request_waits_for_physical_release_and_replays_once(tmp_path, monkeypatch):
    runtime, task, _, _, append, sweep, decide, release = setup_run(tmp_path, monkeypatch)
    assert not sweep()
    append("task.pipeline.stage.dispatched", {**release, "task_map_generation": fixture.GENERATION, "task_pipeline_stage": "impl"})
    append("task.attempt.sent", release)
    runtime.task_store.update(task.id, active_dispatch_id="")
    assert not sweep()
    append("task.attempt.succeeded", release, origin="agent")
    assert not sweep()
    append("task.attempt.succeeded", release)
    first, = sweep()
    assert [x.id for x in sweep()] == [first.id]
    assert decide(prepared_operation_from_checkpoint_event(runtime, first))["status"] == "applied"


def test_relocated_physical_impl_waits_for_latest_delivery(tmp_path, monkeypatch):
    runtime, task, _, _, append, sweep, _, release = setup_run(tmp_path, monkeypatch)
    append("task.pipeline.stage.dispatched", {**release, "task_map_generation": fixture.GENERATION, "task_pipeline_stage": "impl"})
    append("task.attempt.sent", release)
    append("task.attempt.succeeded", release)
    retry = {**release, "attempt_id": "second-attempt", "dispatch_id": "second-delivery"}
    append("task.attempt.sent", retry)
    runtime.task_store.update(task.id, active_dispatch_id="")
    assert not sweep()
    append("task.attempt.succeeded", {**retry, "attempt_id": "other"})
    assert not sweep()
    append("task.attempt.succeeded", retry)
    assert len(sweep()) == 1


@pytest.mark.parametrize("terminal", ["task.attempt.failed", "task.attempt.deadlettered", "task.attempt.superseded"])
def test_terminal_without_success_leaves_normal_oa_recovery_available(tmp_path, monkeypatch, terminal):
    runtime, task, _, _, append, sweep, _, release = setup_run(tmp_path, monkeypatch, settle_impl=False)
    append("task.pipeline.stage.dispatched", {**release, "task_map_generation": fixture.GENERATION, "task_pipeline_stage": "impl"})
    append("task.attempt.sent", release)
    append(terminal, {**release, "status": "expired", "reason": "lease_expired"})
    # A logical result may arrive after transport failure. The existing pipeline
    # decided OA scope is needed, so the physical fence cannot hide that decision.
    append("workflow.call.result.admitted", {"task_id": task.id, "operation_id": release["operation_id"],
           "control_result_ref": {"source_event_id": "build-result"}})
    append("workflow.operation.settled", {"task_id": task.id, "operation_id": release["operation_id"]})
    runtime.task_store.update(task.id, active_dispatch_id="")
    request, = sweep()
    ctx = prepared_operation_from_checkpoint_event(runtime, request).context.input_body["checkpoint_context"]
    assert "verify_assignment_release_event_id" not in ctx


@pytest.mark.parametrize("variation", ["correct", "expired", "other_actor", "other_attempt", "other_source", "dependency", "new_dispatch", "new_operation", "new_delivery"])
def test_rejected_legacy_checkpoint_requires_the_same_settled_predecessor(tmp_path, monkeypatch, variation):
    runtime, task, context, generation, append, sweep, decide, release = setup_run(tmp_path, monkeypatch)
    legacy = _request_correction(runtime, task, context, generation, {})
    runtime.task_store.update(task.id, active_dispatch_id="")
    decision = decide(legacy)
    assert decision["reason"] == "execution_target_changed:" + task.id
    assert len(sweep()) == 1
    result = dict(release)
    if variation == "other_attempt":
        result["attempt_id"] += "-other"
    if variation == "other_source":
        result["source_event_id"] = "unrelated"
    expected_type = "task.attempt.failed" if variation == "expired" else "task.attempt.succeeded"
    if variation == "expired":
        result.update(status="expired", reason="lease_expired")
    released = append(expected_type, result, actor="reader" if variation == "other_actor" else "orchestrator")
    if variation == "dependency":
        runtime.task_store.update(task.id, blocked_by=["other-task"])
    if variation == "new_dispatch":
        runtime.task_store.update(task.id, active_dispatch_id="physical-p3")
    if variation == "new_delivery":
        append("task.attempt.sent", {**release, "attempt_id": "another-physical", "dispatch_id": "other-delivery"})
    if variation == "new_operation":
        append("workflow.operation.requested", {"task_id": task.id, "operation_id": "next",
               "task_pipeline_stage": "impl", "operation_generation": 3, "task_map_generation": context["task_map_generation"]})
    requests = sweep()
    if variation == "dependency":
        # A real change gets the ordinary new contract revision, never the old release authority.
        assert len(requests) == 2
        fresh = prepared_operation_from_checkpoint_event(runtime, requests[-1])
        data = fresh.context.input_body["checkpoint_context"]
        assert data["verify_assignment_revision"] != legacy.context.input_body["checkpoint_context"]["verify_assignment_revision"]
        assert "verify_assignment_release_event_id" not in data
        return
    if variation != "correct":
        assert len(requests) == 1
        return
    original, replacement = requests
    assert original.payload["operation_id"] == legacy.operation_id != replacement.payload["operation_id"]
    assert [e.id for e in sweep()] == [original.id, replacement.id]
    repaired = prepared_operation_from_checkpoint_event(runtime, replacement)
    assert repaired.context.input_body["checkpoint_context"]["verify_assignment_release_event_id"] == released.id
    assert decide(repaired)["status"] == "applied"
