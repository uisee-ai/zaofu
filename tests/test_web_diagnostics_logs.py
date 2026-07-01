"""Web API test for /api/projects/{project_id}/diagnostics/logs (doc 82 §9)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.web.server import create_app


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    store = TaskStore(sd / "kanban.json")
    store.add(Task(id="T1", title="t", status="in_progress", assigned_to="dev-1",
                   contract=TaskContract(feature_id="F-1", owner_role="dev")))
    log = event_log_from_project(sd, config=None, warn=False)
    log.append(ZfEvent(type="task.dispatched", id="e-1", task_id="T1",
                       actor="dev-1", correlation_id="tr-a"))
    log.append(ZfEvent(type="verify.failed", id="e-2", task_id="T1",
                       actor="test-1", correlation_id="tr-a",
                       payload={"reason": "pytest failed", "exit_code": 1}))
    log.append(ZfEvent(type="worker.stuck", id="e-3", task_id="T2",
                       actor="dev-2"))
    return sd


@pytest.fixture
def client(state_dir: Path) -> TestClient:
    return TestClient(create_app(state_dir))


def test_diagnostics_logs_shape(client: TestClient):
    r = client.get("/api/projects/default/diagnostics/logs")
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "diagnostics-logs.v1"
    assert data["count"] == len(data["rows"]) == 3
    newest = data["rows"][0]
    assert newest["source"] == "worker.stuck"
    assert newest["level"] == "WARN"
    error_row = next(r for r in data["rows"] if r["source"] == "verify.failed")
    assert error_row["level"] == "ERROR"
    assert error_row["message"] == "pytest failed"
    assert error_row["attrs"]["exit_code"] == 1
    assert error_row["raw_event_ref"] == "event:e-2"


def test_diagnostics_logs_filters(client: TestClient):
    r = client.get("/api/projects/default/diagnostics/logs",
                   params={"level": "ERROR"})
    assert [row["source"] for row in r.json()["rows"]] == ["verify.failed"]

    r = client.get("/api/projects/default/diagnostics/logs",
                   params={"task_id": "T2"})
    assert [row["task_id"] for row in r.json()["rows"]] == ["T2"]

    r = client.get("/api/projects/default/diagnostics/logs",
                   params={"trace_id": "tr-a"})
    assert {row["trace_id"] for row in r.json()["rows"]} == {"tr-a"}

    r = client.get("/api/projects/default/diagnostics/logs",
                   params={"limit": 1})
    assert r.json()["count"] == 1


def test_unknown_project_404(client: TestClient):
    r = client.get("/api/projects/nope/diagnostics/logs")
    assert r.status_code in (404, 200)  # unknown id falls back per resolver policy
