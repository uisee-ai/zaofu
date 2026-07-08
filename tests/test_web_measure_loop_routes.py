"""Web API tests for measure-loop.v1 routes."""

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
    (sd / "feature_list.json").write_text("[]")
    store = TaskStore(sd / "kanban.json")
    store.add(Task(
        id="T1",
        title="gateway",
        status="backlog",
        contract=TaskContract(feature_id="F-1", owner_role="dev"),
    ))
    log = event_log_from_project(sd, config=None, warn=False)
    log.append(ZfEvent(type="task.dispatched", id="dispatch-1", task_id="T1", payload={"feature_id": "F-1"}))
    log.append(ZfEvent(type="static_gate.failed", id="gate-fail", task_id="T1", payload={"feature_id": "F-1"}))
    return sd


@pytest.fixture
def client(state_dir: Path) -> TestClient:
    return TestClient(create_app(state_dir))


def test_measure_loop_endpoint(client: TestClient) -> None:
    response = client.get("/api/projects/default/measure/loops?feature_id=F-1&lens=verification")

    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "measure-loop.v1"
    assert data["active_lens"] == "verification"
    assert data["metrics"][0]["label"] == "Gate Pass"
    assert data["stages"][0]["label"] == "Dev Done"
    assert data["source_projection_refs"]


def test_measure_loop_endpoint_is_read_only(client: TestClient, state_dir: Path) -> None:
    kanban = state_dir / "kanban.json"
    before = (kanban.stat().st_mtime_ns, kanban.read_bytes())

    response = client.get("/api/projects/default/measure/loops")

    after = (kanban.stat().st_mtime_ns, kanban.read_bytes())
    assert response.status_code == 200
    assert before == after


def test_loop_view_endpoint(client: TestClient) -> None:
    response = client.get("/api/projects/default/loop-view")

    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "loop-view.v1"
    assert data["run"]["promise"]["source"] == "generic fallback"
    assert "delivery" in data["loops"]
    assert data["tasks"]  # T1 dispatch 产生 attempt 行
