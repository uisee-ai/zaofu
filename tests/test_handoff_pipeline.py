"""Task detail and recovery summaries share the replayed pipeline responsibility."""
import pytest

from tests.test_task_pipeline_projection import _event, _generation, _operation_events, _policy
from zf.core.config.schema import ProjectConfig, WorkflowConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.core.task.schema import Task, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.handoff_summary import project_handoff_summary
from zf.web.projections.tasks import _task_detail


def _setup(tmp_path, stage="verify", generation=1):
    state = tmp_path / "custom-state"
    state.mkdir()
    config = ZfConfig(project=ProjectConfig(name="handoff"),
                      workflow=WorkflowConfig(flow_metadata={"task_pipeline": _policy()}))
    task = Task(id="TASK-A", title="Handoff", status="in_progress", assigned_to="impl-2",
                active_dispatch_id="current", execution_binding=TaskExecutionBinding(workflow_run_id="run-1"))
    TaskStore(state / "kanban.json").add(task)
    events = [_generation(), *_operation_events()]
    for event in events[1:]:
        event.payload.update(task_pipeline_stage=stage, operation_generation=generation)
    events += [_event("dispatch", "task.dispatched", payload={
        "dispatch_id": "current", "task_pipeline_stage": stage}),
        _event("pipeline-dispatch", "task.pipeline.stage.dispatched", payload={
            **events[1].payload, "dispatch_id": "current", "attempt_id": "attempt-current",
            "target_ref": "target-current", "canonical_success_event": f"task.pipeline.{stage}.completed"}),
        _event("old-result", "dev.build.done", payload={"dispatch_id": "obsolete"})]
    writer = EventWriter(EventLog(state / "events.jsonl"))
    for event in events:
        writer.append(event)
    return state, config, task, writer


@pytest.mark.parametrize("stage,generation", [("impl", 1), ("verify", 1), ("impl", 2), ("verify", 2), ("acceptance_review", 2)])
def test_pipeline_handoff_uses_current_operation_despite_late_legacy_result(tmp_path, stage, generation):
    state, config, task, _ = _setup(tmp_path, stage, generation)
    before = {p: p.read_bytes() for p in state.rglob("*") if p.is_file() and p.suffix != ".lock"}
    result = project_handoff_summary(state, task.id, config=config)
    replay = project_handoff_summary(state, task.id, config=config)
    assert result["current_stage"] == replay["current_stage"] == stage
    assert result["owner"] == {"role": stage, "instance_id": "impl-2"}
    assert result["next_required_event"] == f"task.pipeline.{stage}.completed"
    assert result["pipeline"]["attempt_id"] == "attempt-current"
    assert result["pipeline"]["target_ref"] == "target-current"
    assert result["pipeline"]["workspace"]["status"] == "unavailable"
    assert "pipeline-dispatch" in result["source_event_ids"]
    assert "old-result" not in result["source_event_ids"]
    assert not result["completed"]
    assert before == {p: p.read_bytes() for p in state.rglob("*") if p.is_file() and p.suffix != ".lock"}


def test_detail_route_and_handoff_show_same_pipeline_stage(tmp_path):
    state, config, task, _ = _setup(tmp_path)
    detail = _task_detail(state, task.id, config=config, project_root=tmp_path)
    assert detail["handoff_summary"]["current_stage"] == "verify"
    assert detail["execution_route"]["current_stage"] == "verify"
    assert detail["task_run_panel"]["current_stage"] == "verify"


def test_new_run_binding_does_not_reuse_previous_generation(tmp_path):
    state, config, task, _ = _setup(tmp_path)
    TaskStore(state / "kanban.json").update(task.id,
        execution_binding=TaskExecutionBinding(workflow_run_id="new-run"))
    result = project_handoff_summary(state, task.id, config=config)
    assert result["current_stage"] == "unknown"
    assert result["pipeline"]["status"] == "unavailable"
    assert not result["owner"]["instance_id"]
    assert not result["next_required_event"]


def test_settlement_waits_for_next_stage_without_claiming_task_done(tmp_path):
    state, config, task, writer = _setup(tmp_path, "impl")
    writer.append(_event("admitted", "workflow.call.result.admitted", payload={
        "operation_id": "op-a-impl-1", "semantic_verdict": "passed"}))
    writer.append(_event("settled", "workflow.operation.settled", payload={
        "operation_id": "op-a-impl-1", "request_hash": "request-sha",
        "semantic_verdict": "passed", "admitted_call_result_ref": {"ref": "result", "sha256": "sha"}}))
    result = project_handoff_summary(state, task.id, config=config)
    assert result["current_stage"] == "verify_ready"
    assert result["next_required_event"] == "task.pipeline.stage.dispatched"
    assert not result["owner"]["instance_id"]
    assert TaskStore(state / "kanban.json").get(task.id).status == "in_progress"
    assert result["completed"][0]["kind"] == "admitted_result"
    assert "passed" in result["completed"][0]["summary"]


@pytest.mark.parametrize("status", ["done", "cancelled"])
def test_terminal_task_does_not_show_old_active_worker(tmp_path, status):
    state, config, task, _ = _setup(tmp_path)
    TaskStore(state / "kanban.json").update(task.id, status=status)
    result = project_handoff_summary(state, task.id, config=config)
    assert result["current_stage"] == status
    assert not result["next_required_event"]
    assert not result["owner"]["instance_id"]
