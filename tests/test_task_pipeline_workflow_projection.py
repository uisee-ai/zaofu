from dataclasses import replace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.kanban_projection import workflow_projection
from zf.core.task.lifecycle import derive_phase
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


def _event(kind, generation, stage="impl", **extra):
    return ZfEvent(type=kind, actor="orchestrator", origin="kernel", task_id="T1",
        payload={"workflow_run_id": "run-1", "task_map_generation": "map-1",
                 "operation_generation": generation, "task_pipeline_stage": stage,
                 "dispatch_id": f"{stage}-{generation}", **extra})


def _case():
    task = Task(id="T1", title="rework", status="in_progress",
                assigned_to="dev-lane-0", active_dispatch_id="impl-3")
    events = [
        _event("task.pipeline.stage.dispatched", 2),
        _event("dev.build.done", 2),
        _event("static_gate.passed", 2),
        _event("task.pipeline.stage.dispatched", 2, "verify"),
        _event("workflow.operation.blocked", 2, "verify",
               reason="provider_stop:provider_policy_rejected"),
        _event("task.pipeline.stage.dispatched", 3),
    ]
    return task, events


@pytest.mark.parametrize("late_kind", [None, "verify.passed", "verify.failed", "judge.passed"])
def test_new_impl_does_not_inherit_previous_generation_results(late_kind):
    task, events = _case()
    if late_kind:
        events.append(_event(late_kind, 2, "verify"))
    original = list(events)
    assert derive_phase(task, events) == "impl_running"
    view = workflow_projection(task, events, judge_configured=True)
    assert view.workflow_phase == "impl"
    assert view.verify_state == "empty"
    assert view.judge_state == "waiting"
    assert view.impl_exit_gate_state == "not_configured"
    assert events == original


def test_active_dispatch_pins_scope_despite_unrelated_or_stale_dispatch():
    task, events = _case()
    events.extend([
        replace(_event("task.pipeline.stage.dispatched", 5), task_id="OTHER"),
        _event("task.pipeline.stage.dispatched", 2, "verify"),
    ])
    assert derive_phase(task, events) == "impl_running"
    assert workflow_projection(task, events).workflow_phase == "impl"


@pytest.mark.parametrize("field", ["workflow_run_id", "task_map_generation"])
def test_same_number_from_another_run_or_plan_does_not_supply_verdict(field):
    task, events = _case()
    events.append(_event("verify.passed", 3, "verify", **{field: "other"}))
    assert derive_phase(task, events) == "impl_running"
    assert workflow_projection(task, events).verify_state == "empty"


def test_unbound_dispatch_does_not_reset_legacy_history():
    task, events = _case()
    task = replace(task, active_dispatch_id="unobserved")
    assert derive_phase(task, events) == "static_gate_passed"
    assert workflow_projection(task, events).workflow_phase == "verify"


def test_new_verifier_keeps_same_generation_impl_results():
    task, events = _case()
    task = replace(task, assigned_to="verify-lane-0", active_dispatch_id="verify-3")
    events.extend([_event("dev.build.done", 3),
                   _event("task.pipeline.stage.dispatched", 3, "verify")])
    assert derive_phase(task, events) == "test_running"
    view = workflow_projection(task, events)
    assert view.workflow_phase == "verify"
    assert view.verify_state == "pending"
    # Settled/terminal tasks retain the latest generation's genuine evidence.
    events.append(_event("verify.passed", 3, "verify"))
    view = workflow_projection(replace(task, status="done", active_dispatch_id=""), events)
    assert view.workflow_phase == "done"
    assert view.verify_state == "passed"


def test_web_board_and_detail_follow_new_impl_generation(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from zf.web.server import create_app

    state = tmp_path / "state"
    state.mkdir()
    (state / "kanban.json").write_text("[]")
    (state / "feature_list.json").write_text("[]")
    task, events = _case()
    TaskStore(state / "kanban.json").add(task)
    log = EventLog(state / "events.jsonl")
    for event in events:
        log.append(event)
    client = TestClient(create_app(state, project_root=tmp_path))
    detail = client.get("/api/tasks/T1").json()
    assert detail["task"]["workflow_phase"] == "impl"
    assert detail["task"]["kanban_column"] == "in_progress"
    snapshot = client.get("/api/snapshot").json()
    card = next(t for t in snapshot["tasks"] if t["id"] == "T1")
    assert card["workflow_phase"] == "impl"
    assert card["kanban_column"] == "in_progress"
    assert card["verify_state"] == "empty"
    assert TaskStore(state / "kanban.json").get("T1") == task
