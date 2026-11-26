from tests import test_orchestrator_agent_operations as oa
from tests.test_oa_verify_predecessor_release import setup_run
from tests.test_run_admission import _control
from tests.test_task_pipeline_reconciler import _operation, _policy
from zf.core.events import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_advisory_queue import next_advisory_event
from zf.runtime.orchestrator_agent_operations import fail_orchestrator_agent_operation, request_orchestrator_agent_checkpoint
from zf.runtime.orchestrator_agent_transport import dispatch_orchestrator_agent_operation, dispatch_pending_orchestrator_checkpoint
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.workflow_operation import load_workflow_operation


def test_pause_holds_new_scoped_oa_request_until_same_run_resumes(tmp_path, monkeypatch):
    runtime, task, context, _, append, sweep, _, release = setup_run(tmp_path, monkeypatch)
    run = context["workflow_run_id"]
    append("run.goal.started", {"status": "active"})
    assert _control(runtime, action="run-pause", run_id=run)["status"] == "paused"
    append("task.attempt.succeeded", release)
    runtime.task_store.update(task.id, active_dispatch_id="")
    assert not sweep() and not sweep()
    assert _control(runtime, action="run-resume", run_id=run)["status"] == "running"
    first, = sweep()
    assert [e.id for e in sweep()] == [first.id]


def test_already_running_oa_may_sign_its_requested_handoff_during_pause(tmp_path, monkeypatch):
    from tests import test_orchestrator_agent_run_plan as fixture
    from zf.runtime.orchestrator_agent_operations import prepared_operation_from_checkpoint_event

    runtime, task, context, _, append, sweep, decide, release = setup_run(tmp_path, monkeypatch)
    append("run.goal.started", {"status": "active"})
    append("task.attempt.succeeded", release)
    runtime.task_store.update(task.id, active_dispatch_id="")
    request, = sweep()
    prepared = prepared_operation_from_checkpoint_event(runtime, request)
    activate = fixture.activate_orchestrator_agent_operation

    def pause_after_start(runtime, prepared, **params):
        activate(runtime, prepared, **params)
        assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "running"
        assert _control(runtime, action="run-pause", run_id=context["workflow_run_id"])["status"] == "paused"

    monkeypatch.setattr(fixture, "activate_orchestrator_agent_operation", pause_after_start)
    assert decide(prepared)["status"] == "applied"
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "settled"
    admission, = runtime.event_log.query(type="orchestrator.run_plan.admitted")
    assert admission.payload["verify_assignment_task"] == task.id


def test_queued_business_handoff_resumes_once_while_diagnostics_and_other_run_proceed(tmp_path):
    state, log, writer = oa._state(tmp_path)
    transport = oa.RecordingTransport()
    runtime = Orchestrator(state, oa._config(), transport, project_root=tmp_path)
    run = "run-oa-1"
    writer.append(ZfEvent(type="run.goal.started", actor="zf-cli", payload={"run_id": run, "status": "active"}))
    held = oa._request(runtime, checkpoint="pre_impl")
    first, = log.query(type=oa.CHECKPOINT_REQUESTED)
    assert _control(runtime, action="run-pause", run_id=run)["status"] == "paused"
    role = runtime.config.roles[0]
    # Both the original immediate wake and repeated idle sweeps honor the hold.
    dispatch_orchestrator_agent_operation(runtime, role, first)
    dispatch_pending_orchestrator_checkpoint(runtime)
    assert not transport.sent and load_workflow_operation(log, held.operation_id)["status"] == "requested"

    incident = oa._request(runtime, checkpoint="semantic_failure", trigger_id="fault")
    other = request_orchestrator_agent_checkpoint(runtime, checkpoint="pre_impl", checkpoint_policy="blocking",
        workflow_run_id="other", source_event=ZfEvent(type="task_map.ready", correlation_id="other"), payload={
            "workflow_run_id": "other", "result_refs": [{"source_id": "current", **oa._source(runtime)}]})
    for available in (incident, other):
        assert next_advisory_event(log.read_all(), role.instance_id).payload["operation_id"] == available.operation_id
        dispatch_pending_orchestrator_checkpoint(runtime)
        assert load_workflow_operation(log, available.operation_id)["status"] == "running"
        fail_orchestrator_agent_operation(runtime, available, reason="fixture_release", causation_id=first.id)
    assert len(transport.sent) == 2 and load_workflow_operation(log, held.operation_id)["status"] == "requested"
    assert _control(runtime, action="run-resume", run_id=run)["status"] == "running"
    restarted = Orchestrator(state, oa._config(), transport, project_root=tmp_path)
    dispatch_pending_orchestrator_checkpoint(restarted)
    dispatch_pending_orchestrator_checkpoint(restarted)
    assert len(transport.sent) == 3
    assert load_workflow_operation(log, held.operation_id)["status"] == "running"


def test_paused_verify_task_yields_capacity_to_another_ready_run():
    tasks = [Task(id="A", title="held"), Task(id="B", title="active")]
    policy = _policy()
    policy["pools"]["verify"]["capacity"] = 1
    result = TaskPipelineReconciler().reconcile(policy=policy, tasks=tasks, attempts=[],
        operations=[_operation(t.id, "impl", "settled") for t in tasks], held_task_ids=frozenset({"A"}))
    assert [row["task_id"] for row in result["dispatchable"]["verify"]] == ["B"]
    assert next(row for row in result["tasks"] if row["task_id"] == "A")["blockers"] == ["run_paused"]
