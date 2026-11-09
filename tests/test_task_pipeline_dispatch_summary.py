from dataclasses import replace
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_task_pipeline_projection import _generation, _operation_events, _policy
from zf.cli.trace import _delivery_summary, run_dispatch_summary
from zf.core.config.schema import ProjectConfig, WorkflowConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.task_pipeline_dispatch_summary import build_dispatch_summary
from zf.runtime.task_pipeline_projection import build_task_pipeline_projection
from zf.web.task_pipeline_routes import build_task_pipeline_router


def test_settled_failure_is_not_active_despite_task_lifecycle():
    task = Task(id="TASK-A", title="A", status="in_progress")
    events = [_generation(), *_operation_events(terminal=True)]
    events.insert(-1, replace(events[-1], id="failed-result", type="workflow.call.result.admitted",
                             payload={**events[-1].payload, "semantic_verdict": "failed"}))
    projection = build_task_pipeline_projection(policy=_policy(), tasks=[task], events=events)
    summary = build_dispatch_summary(projection, workflow_run_id="run-1")
    assert summary["task_statuses"] == {"in_progress": 1}
    assert summary["execution_statuses"] == {"blocked": 1}
    assert summary["tasks"][0]["latest_operation"]["semantic_verdict"] == "failed"
    assert not summary["tasks"][0]["current_worker"]
    assert build_dispatch_summary(projection, workflow_run_id="different")["tasks"] == []
    assert task.status == "in_progress"


def test_trace_summary_retains_lifecycle_but_exposes_execution_and_plan_ref():
    trace = {"task_map": {"task_map_ref": "artifacts/current-map.json"},
             "execution_graph": {"in_progress_count": 1, "blocked_count": 0},
             "dispatch_summary": {"execution_statuses": {"blocked": 1}}}
    result = _delivery_summary(trace, view="execution-graph")
    assert result["current_task_map_ref"] == "artifacts/current-map.json"
    assert result["tasks"]["in_progress_count"] == 1
    assert result["execution"]["execution_statuses"]["blocked"] == 1


def test_new_passed_impl_supersedes_old_failure_in_summary():
    task = Task(id="TASK-A", title="A", status="in_progress")
    events = [_generation(), *_operation_events(terminal=True)]
    events.insert(-1, replace(events[-1], id="failed-result", type="workflow.call.result.admitted",
                             payload={**events[-1].payload, "semantic_verdict": "failed"}))
    successor = [replace(e, id="next-" + e.id, payload={**e.payload,
        "operation_id": "impl-2", "operation_generation": 2}) for e in _operation_events(terminal=True)]
    successor.insert(-1, replace(successor[-1], id="passed-result", type="workflow.call.result.admitted",
                                payload={**successor[-1].payload, "semantic_verdict": "passed"}))
    summary = build_dispatch_summary(build_task_pipeline_projection(
        policy=_policy(), tasks=[task], events=events + successor))
    assert summary["tasks"][0]["latest_operation"]["operation_id"] == "impl-2"
    assert summary["tasks"][0]["latest_operation"]["semantic_verdict"] == "passed"
    assert summary["execution_statuses"] == {"ready": 1}


def test_cli_and_api_read_same_summary_without_mutating_state(tmp_path, monkeypatch, capsys):
    state = tmp_path / "runtime" / "custom-state"
    state.mkdir(parents=True)
    TaskStore(state / "kanban.json").add(Task(id="TASK-A", title="A", status="in_progress"))
    log = EventLog(state / "events.jsonl")
    for event in [_generation(), *_operation_events()]:
        log.append(event)
    config = ZfConfig(project=ProjectConfig(name="summary", state_dir=str(state)),
                      workflow=WorkflowConfig(flow_metadata={"task_pipeline": _policy()}))
    ctx = SimpleNamespace(state_dir=state, project_root=tmp_path, config=config)
    before = {p: p.read_bytes() for p in state.rglob("*") if p.is_file()}
    monkeypatch.setattr("zf.cli.trace.resolve_project_context", lambda **_: ctx)
    assert run_dispatch_summary(SimpleNamespace(state_dir=str(state), run_id="run-1")) == 0
    import json
    cli = json.loads(capsys.readouterr().out)
    app = FastAPI()
    app.include_router(build_task_pipeline_router(resolve_ctx=lambda _: ctx,
        default_state_dir=state, default_project_root=tmp_path, default_config=config))
    with TestClient(app) as client:
        assert client.get("/api/projects/summary/task-pipeline/summary?workflow_run_id=run-1").json() == cli
        assert client.get("/api/task-pipeline/summary?workflow_run_id=run-1").json() == cli
    assert {p: p.read_bytes() for p in state.rglob("*") if p.is_file() and p.suffix != ".lock"} == {
        p: body for p, body in before.items() if p.suffix != ".lock"
    }
