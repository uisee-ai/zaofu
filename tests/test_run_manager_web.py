from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.web.server import create_app


def test_run_manager_and_goal_api_are_read_only_projections(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="run.goal.started",
        payload={"run_id": "R-WEB", "objective": "refactor hermes"},
    ))
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    manager = client.get("/api/run-manager")
    goal = client.get("/api/run-goal")

    assert manager.status_code == 200
    assert goal.status_code == 200
    body = manager.json()
    assert body["schema_version"] == "run-manager.v1"
    assert body["goal"]["run_id"] == "R-WEB"
    assert body["monitor"]["schema_version"] == "run-manager.monitor.v1"
    assert body["status_explain"]["schema_version"] == "run-status-explain.v1"
    assert body["completion_profile"]["schema_version"] == "run-completion-profile.v1"
    assert body["repair_merge_queue"]["schema_version"] == "repair-merge-queue.v1"
    assert body["timeline"]["schema_version"] == "run-manager.timeline.v1"
    goal_body = goal.json()
    assert goal_body["objective"] == "refactor hermes"
    assert goal_body["delivery_phase"] == "not_started"
    assert goal_body["open_feedback_count"] == 0
    assert goal_body["pending_handoff_count"] == 0
    assert goal_body["attempt_handoff_schema_version"] == "attempt-handoff-snapshot.v1"


def test_project_scoped_run_manager_api(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(type="loop.started"))
    client = TestClient(create_app(state_dir, project_root=tmp_path))
    project_id = "default"
    projects = client.get("/api/workspace/projects").json()
    if projects.get("projects"):
        project_id = projects["projects"][0]["project_id"]

    response = client.get(f"/api/projects/{project_id}/run-manager")

    assert response.status_code == 200
    assert response.json()["schema_version"] == "run-manager.v1"


def test_state_api_reconciles_residual_tasks_after_run_completed(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-R4-GAP",
        title="residual gap",
        status="in_progress",
        assigned_to="verify-lane-0",
    ))
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="run.completed",
        actor="run-manager",
        payload={"status": "passed", "release_status": "not_shipped"},
    ))
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    response = client.get("/api/state")

    assert response.status_code == 200
    task = response.json()["tasks"][0]
    assert task["status"] == "in_progress"
    assert task["display_status"] == "done"
    assert task["kanban_column"] == "done"
    assert task["projection_reconciled"] is True
    assert task["projection_reconcile_reason"] == "run_completed"
    assert TaskStore(state_dir / "kanban.json").get("TASK-R4-GAP").status == "in_progress"
