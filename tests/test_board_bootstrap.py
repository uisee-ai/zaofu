from fastapi.testclient import TestClient
import pytest

from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.web import board_bootstrap as module, server


def test_real_route_reads_tasks_without_event_projection_or_unknown_metrics(tmp_path, monkeypatch):
    state = tmp_path / "custom-state"
    store = TaskStore(state / "kanban.json")
    store.add(Task(id="active", title="TOKEN=private", status="in_progress", assigned_to="dev-1"))
    store.add(Task(id="archived", title="finished", status="done"))
    (state / "events.jsonl").touch()
    app = server.create_app(state, project_root=tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("TaskStore first paint scanned events or built a complete projection")
    from zf.web.projections import read_model, events
    monkeypatch.setattr(read_model, "rebuild", forbidden)
    monkeypatch.setattr(events, "_events_with_seq", forbidden)
    monkeypatch.setattr(server, "_snapshot_slice", forbidden)
    before = (state / "kanban.json").read_bytes()
    result = TestClient(app).get("/api/projects/default/board-tasks")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["source"] == "TaskStore" and body["derived_state"] == "pending"
    assert body["project_id"] == "default"
    assert body["tasks"][0]["status"] == "in_progress"
    assert "private" not in result.text
    assert [task["id"] for task in body["archive_tasks"]] == ["archived"]
    assert not {"cost", "metrics_snapshot", "runtime", "seq"} & body.keys()
    assert (state / "kanban.json").read_bytes() == before


def test_bootstrap_retries_store_change_and_never_returns_a_mixed_list(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(id="one", title="before", status="pending"))
    original = TaskStore.list_all_with_archive
    calls = []
    def changed(self, **kwargs):
        value = original(self, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            self.update("one", title="after")
        return value
    monkeypatch.setattr(TaskStore, "list_all_with_archive", changed)
    data = module.board_tasks(tmp_path, project_id="project")
    assert data["tasks"][0]["title"] == "after" and len(calls) == 2
    counter = iter(range(10))
    monkeypatch.setattr(module, "_identity", lambda _: next(counter))
    with pytest.raises(module.BoardTasksChanged):
        module.board_tasks(tmp_path, project_id="project")


def test_corrupt_task_file_is_unavailable_not_empty(tmp_path):
    (tmp_path / "events.jsonl").touch()
    app = server.create_app(tmp_path, project_root=tmp_path)
    (tmp_path / "kanban.json").write_text("{invalid")
    response = TestClient(app).get("/api/projects/default/board-tasks")
    assert response.status_code == 503 and "tasks" not in response.json()
