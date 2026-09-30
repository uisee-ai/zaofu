"""S6 conformance case for EventLog/Store → API truth projection."""

from __future__ import annotations

from pathlib import Path
import json
import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.web.server import create_app

from tests.testkit.oracles import assert_required_events
from tests.testkit.scenarios import isolated_project
from tests.test_web_sse import _collect


def test_openapi_contract_has_complete_operation_and_path_parameter_metadata(
    tmp_path: Path,
) -> None:
    """Every documented route must be callable from its generated contract."""
    project = isolated_project(tmp_path, run_id="RUN-S6-OPENAPI")
    client = TestClient(create_app(project.state_dir, project_root=tmp_path))

    response = client.get("/openapi.json")

    assert response.status_code == 200
    document = response.json()
    assert document["openapi"].startswith("3.")
    operation_ids: list[str] = []
    methods = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
    for path, path_item in document["paths"].items():
        required_path_params = set(re.findall(r"{([^}]+)}", path))
        for method, operation in path_item.items():
            if method not in methods:
                continue
            operation_ids.append(operation["operationId"])
            assert operation["responses"], f"{method.upper()} {path} has no responses"
            declared_path_params = {
                item["name"]
                for item in operation.get("parameters", [])
                if item.get("in") == "path"
            }
            assert required_path_params <= declared_path_params
    assert len(operation_ids) == len(set(operation_ids))


@pytest.mark.parametrize(
    "status",
    [
        "backlog",
        "in_progress",
        "review",
        "verify",
        "blocked",
        "failed",
        "cancelled",
        "done",
    ],
)
def test_api_projection_matches_canonical_store_for_each_task_status(
    tmp_path: Path,
    status: str,
) -> None:
    project = isolated_project(
        tmp_path,
        run_id="RUN-S6-TRUTH",
        task_id="TASK-S6",
    )
    state_dir = project.state_dir
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="session.started", actor="zf-cli"))
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id=project.task_id, title="projection task", status=status))
    config = ZfConfig(
        project=ProjectConfig(name="s6-truth"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )
    client = TestClient(
        create_app(state_dir, config=config, project_root=tmp_path),
    )

    response = client.get("/api/state")

    assert response.status_code == 200
    rows = [row for row in response.json()["tasks"] if row["id"] == project.task_id]
    assert len(rows) == 1
    assert rows[0]["status"] == store.get(project.task_id).status == status
    assert_required_events(log, ["session.started"])
    assert (tmp_path / ".zf").exists() is False


def test_api_reads_explicit_testkit_state_dir_and_not_checkout_default(
    tmp_path: Path,
) -> None:
    project = isolated_project(tmp_path, run_id="RUN-S6-DIR", task_id="TASK-S6-DIR")
    store = TaskStore(project.state_dir / "kanban.json")
    store.add(Task(id=project.task_id, title="state dir", status="backlog"))
    client = TestClient(
        create_app(project.state_dir, project_root=tmp_path),
    )

    response = client.get("/api/state")

    assert response.status_code == 200
    assert response.json()["tasks"][0]["id"] == project.task_id
    assert not (tmp_path / ".zf" / "kanban.json").exists()


def test_api_does_not_cross_project_scope_for_same_task_id(
    tmp_path: Path,
) -> None:
    """Project projections must isolate identical task ids by state root."""
    project_a = isolated_project(
        tmp_path / "project-a",
        run_id="RUN-S6-A",
        task_id="TASK-S6-SAME-ID",
    )
    project_b = isolated_project(
        tmp_path / "project-b",
        run_id="RUN-S6-B",
        task_id="TASK-S6-SAME-ID",
    )
    TaskStore(project_a.state_dir / "kanban.json").add(
        Task(id=project_a.task_id, title="project A", status="backlog"),
    )
    TaskStore(project_b.state_dir / "kanban.json").add(
        Task(id=project_b.task_id, title="project B", status="done"),
    )

    response_a = TestClient(
        create_app(project_a.state_dir, project_root=project_a.root),
    ).get("/api/state")
    response_b = TestClient(
        create_app(project_b.state_dir, project_root=project_b.root),
    ).get("/api/state")

    assert response_a.status_code == response_b.status_code == 200
    row_a = next(row for row in response_a.json()["tasks"] if row["id"] == project_a.task_id)
    row_b = next(row for row in response_b.json()["tasks"] if row["id"] == project_b.task_id)
    assert row_a["title"] == "project A"
    assert row_a["status"] == "backlog"
    assert row_b["title"] == "project B"
    assert row_b["status"] == "done"


def test_terminal_task_is_exposed_by_archive_projection(
    tmp_path: Path,
) -> None:
    """A terminal canonical task remains queryable through the archive API."""
    project = isolated_project(
        tmp_path,
        run_id="RUN-S6-ARCHIVE",
        task_id="TASK-S6-ARCHIVE",
    )
    store = TaskStore(project.state_dir / "kanban.json")
    store.add(Task(id=project.task_id, title="archived task", status="backlog"))
    terminal = store.update(project.task_id, status="done")
    assert terminal is not None and terminal.status == "done"

    client = TestClient(
        create_app(project.state_dir, project_root=tmp_path),
    )
    response = client.get("/api/archives/tasks")

    assert response.status_code == 200
    rows = [row for row in response.json() if row["id"] == project.task_id]
    assert len(rows) == 1
    assert rows[0]["status"] == "done"
    assert rows[0]["kanban_column"] == "done"
    assert not (tmp_path / ".zf" / "kanban.json").exists()


def test_authorized_archive_task_moves_canonical_task_to_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The archive action must use the kernel path and emit one archive fact."""

    project = isolated_project(
        tmp_path,
        run_id="RUN-S6-ACTION",
        task_id="TASK-S6-ACTION",
    )
    store = TaskStore(project.state_dir / "kanban.json")
    store.add(Task(id=project.task_id, title="archive action", status="backlog"))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "testkit-action-token")
    client = TestClient(
        create_app(project.state_dir, project_root=tmp_path),
    )

    denied = client.post(
        "/api/actions/archive-task",
        json={"task_id": project.task_id, "status": "done"},
    )
    assert denied.status_code in {401, 403}
    assert store.get(project.task_id).status == "backlog"

    archived = client.post(
        "/api/actions/archive-task",
        headers={
            "x-zf-web-token": "testkit-action-token",
            "x-idempotency-key": "s6-archive-once",
        },
        json={"task_id": project.task_id, "status": "done"},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "completed"
    replay = client.post(
        "/api/actions/archive-task",
        headers={
            "x-zf-web-token": "testkit-action-token",
            "x-idempotency-key": "s6-archive-once",
        },
        json={"task_id": project.task_id, "status": "done"},
    )
    assert replay.status_code == 200
    assert replay.json()["idempotency"]["status"] == "replayed"
    conflict = client.post(
        "/api/actions/archive-task",
        headers={
            "x-zf-web-token": "testkit-action-token",
            "x-idempotency-key": "s6-archive-once",
        },
        json={"task_id": project.task_id, "status": "cancelled"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["status"] == "idempotency_key_conflict"
    all_tasks = TaskStore(project.state_dir / "kanban.json").list_all_with_archive()
    current = next(item for item in all_tasks if item.id == project.task_id)
    assert current.status == "done"
    events = EventLog(project.state_dir / "events.jsonl").read_all()
    assert len([event for event in events if event.type == "task.archived"]) == 1
    assert len([event for event in events if event.type == "runtime.action.completed"]) == 1
    assert len([event for event in events if event.type == "runtime.action.rejected"]) == 1


def test_authorized_update_task_is_idempotent_and_keeps_event_store_consistent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Update action must be token-gated, replay-safe and canonicalized once."""
    project = isolated_project(
        tmp_path,
        run_id="RUN-S6-UPDATE",
        task_id="TASK-S6-UPDATE",
    )
    store = TaskStore(project.state_dir / "kanban.json")
    store.add(Task(id=project.task_id, title="update action", status="backlog"))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "testkit-update-token")
    client = TestClient(
        create_app(project.state_dir, project_root=tmp_path),
    )

    denied = client.post(
        "/api/actions/update-task",
        json={"task_id": project.task_id, "status": "in_progress"},
    )
    assert denied.status_code in {401, 403}
    assert store.get(project.task_id).status == "backlog"

    headers = {
        "x-zf-web-token": "testkit-update-token",
        "x-idempotency-key": "s6-update-once",
    }
    updated = client.post(
        "/api/actions/update-task",
        headers=headers,
        json={"task_id": project.task_id, "status": "in_progress"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "completed"
    replay = client.post(
        "/api/actions/update-task",
        headers=headers,
        json={"task_id": project.task_id, "status": "in_progress"},
    )
    assert replay.status_code == 200
    assert replay.json()["idempotency"]["status"] == "replayed"
    conflict = client.post(
        "/api/actions/update-task",
        headers=headers,
        json={"task_id": project.task_id, "status": "review"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["status"] == "idempotency_key_conflict"

    current = TaskStore(project.state_dir / "kanban.json").get(project.task_id)
    assert current is not None and current.status == "in_progress"
    events = EventLog(project.state_dir / "events.jsonl").read_all()
    updates = [event for event in events if event.type == "task.updated"]
    assert len(updates) == 1
    assert updates[0].payload["updates"]["status"] == "in_progress"
    assert len([event for event in events if event.type == "runtime.action.completed"]) == 1
    assert len([event for event in events if event.type == "runtime.action.rejected"]) == 1


def test_regression_capture_and_replay_are_token_gated_idempotent_and_evidenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression actions must cross the same controlled API truth boundary.

    This exercises the additional product-facing action pair that is not
    covered by the browser journey: authorization, idempotent replay,
    persisted case evidence, and replay verdict events all remain observable
    in the canonical EventLog.
    """
    project = isolated_project(
        tmp_path,
        run_id="RUN-S6-REGRESSION",
        task_id="TASK-S6-REGRESSION",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "testkit-regression-token")
    client = TestClient(create_app(project.state_dir, project_root=tmp_path))

    payload = {
        "task_id": project.task_id,
        "case_id": "rc-s6-1",
        "feature_id": "F-S6",
        "assertions": ["scope_violation==0", "rework<=1"],
    }
    denied = client.post("/api/actions/capture-regression-case", json=payload)
    assert denied.status_code in {401, 403}

    headers = {
        "x-zf-web-token": "testkit-regression-token",
        "x-idempotency-key": "s6-regression-capture-once",
    }
    captured = client.post(
        "/api/actions/capture-regression-case",
        headers=headers,
        json=payload,
    )
    assert captured.status_code == 201
    assert captured.json()["result"]["case_id"] == "rc-s6-1"
    artifact = project.state_dir / "artifacts" / "regression" / "rc-s6-1.json"
    assert artifact.is_file() and artifact.stat().st_size > 0

    replay_capture = client.post(
        "/api/actions/capture-regression-case",
        headers=headers,
        json=payload,
    )
    # The idempotency store replays the original response status (201 for a
    # newly captured artifact) while adding an explicit replay marker.
    assert replay_capture.status_code == 201
    assert replay_capture.json()["idempotency"]["status"] == "replayed"
    conflict = client.post(
        "/api/actions/capture-regression-case",
        headers=headers,
        json={**payload, "assertions": ["scope_violation==1"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["status"] == "idempotency_key_conflict"

    replay = client.post(
        "/api/actions/replay-regression-case",
        headers={
            "x-zf-web-token": "testkit-regression-token",
            "x-idempotency-key": "s6-regression-replay-once",
        },
        json={"case_id": "rc-s6-1"},
    )
    assert replay.status_code == 200
    assert replay.json()["result"]["passed"] is True

    log = EventLog(project.state_dir / "events.jsonl")
    events = log.read_all()
    assert len([event for event in events if event.type == "regression.case.captured"]) == 1
    assert len([event for event in events if event.type == "regression.case.replayed"]) == 1
    assert len([event for event in events if event.type == "runtime.action.completed"]) == 2
    assert len([event for event in events if event.type == "runtime.action.rejected"]) == 1
    assert_required_events(log, ["regression.case.captured", "regression.case.replayed"])


@pytest.mark.asyncio
async def test_eventlog_store_api_and_sse_share_one_task_identity(
    tmp_path: Path,
) -> None:
    """The backend truth chain carries the same task/status to API and SSE."""
    project = isolated_project(
        tmp_path,
        run_id="RUN-S6-CHAIN",
        task_id="TASK-S6-CHAIN",
    )
    log = project.event_log()
    created = project.emit("task.created", payload={"status": "backlog"})
    store = TaskStore(project.state_dir / "kanban.json")
    store.add(Task(id=project.task_id, title="truth-chain", status="in_progress"))
    changed = project.emit(
        "task.status.changed",
        payload={"from": "backlog", "to": "in_progress"},
        causation_id=created.id,
    )
    client = TestClient(
        create_app(project.state_dir, project_root=tmp_path),
    )

    response = client.get("/api/state")
    assert response.status_code == 200
    task_rows = [
        row for row in response.json()["tasks"] if row["id"] == project.task_id
    ]
    assert len(task_rows) == 1
    assert task_rows[0]["status"] == "in_progress"
    assert store.get(project.task_id).status == "in_progress"

    stream = await _collect(project.state_dir, disconnect_after=2)
    data_lines = [
        line[len("data: "):]
        for line in stream.decode().splitlines()
        if line.startswith("data: ")
    ]
    stream_events = [json.loads(line) for line in data_lines]
    by_type = {event["type"]: event for event in stream_events}
    assert "id: 1" in stream.decode()
    assert "id: 2" in stream.decode()
    assert by_type["task.status.changed"]["task_id"] == project.task_id
    assert created.id == by_type["task.created"]["id"]
    assert changed.id == by_type["task.status.changed"]["id"]
    assert_required_events(log, ["task.created", "task.status.changed"])
