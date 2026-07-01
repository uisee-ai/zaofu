from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from zf.core.config.loader import load_config
from zf.core.config.project_context import ProjectContext
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.workspace.registry import WorkspaceRegistry
from zf.web.server import create_app


def _project(root: Path, name: str, task_id: str, trace_id: str) -> ProjectContext:
    root.mkdir()
    state_dir = root / ".zf"
    state_dir.mkdir()
    (root / "zf.yaml").write_text(
        f'version: "1.0"\nproject:\n  name: {name}\n  state_dir: .zf\n',
        encoding="utf-8",
    )
    (state_dir / "feature_list.json").write_text("[]", encoding="utf-8")
    TaskStore(state_dir / "kanban.json").add(Task(id=task_id, title=task_id, status="done"))
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(type="dev.impl.completed", id=f"evt-{task_id}", task_id=task_id, correlation_id=trace_id))
    return ProjectContext(
        project_root=root,
        config_path=root / "zf.yaml",
        config=load_config(root / "zf.yaml"),
        state_dir=state_dir,
    )


def test_project_scoped_snapshot_traces_do_not_cross_state_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    project_a = _project(tmp_path / "repo-a", "repo-a", "A-1", "trace-a")
    project_b = _project(tmp_path / "repo-b", "repo-b", "B-1", "trace-b")
    registry_project_b = WorkspaceRegistry().upsert_context(project_b)

    client = TestClient(create_app(
        project_a.state_dir,
        config=project_a.config,
        project_root=project_a.project_root,
    ))
    project_a_id = client.get("/api/workspace/projects").json()["server_default_project_id"]

    snapshot_a = client.get(f"/api/projects/{project_a_id}/snapshot").json()
    snapshot_b = client.get(f"/api/projects/{registry_project_b.project_id}/snapshot").json()

    assert snapshot_a["project"]["project_id"] == project_a_id
    assert snapshot_b["project"]["project_id"] == registry_project_b.project_id
    assert {trace["trace_id"] for trace in snapshot_a["traces"]} == {"trace-a"}
    assert {trace["trace_id"] for trace in snapshot_b["traces"]} == {"trace-b"}
    assert {task["id"] for task in snapshot_a["tasks"]} == {"A-1"}
    assert {task["id"] for task in snapshot_b["tasks"]} == {"B-1"}
