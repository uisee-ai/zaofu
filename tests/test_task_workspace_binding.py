from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    WorkdirConfig,
    ZfConfig,
)
from zf.runtime.task_workspaces import TaskWorkspaceError, TaskWorkspaceManager


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repo(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Task Workspace Test")
    _git(root, "config", "user.email", "task@example.com")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "base")
    return _git(root, "rev-parse", "HEAD")


def _manager(tmp_path: Path) -> tuple[TaskWorkspaceManager, RoleConfig, str]:
    base = _repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    role = RoleConfig(name="impl", backend="mock", role_kind="writer")
    config = ZfConfig(
        project=ProjectConfig(name="task-pipeline"),
        roles=[role],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    return (
        TaskWorkspaceManager(
            state_dir=state_dir,
            project_root=tmp_path,
            config=config,
        ),
        role,
        base,
    )


def _prepare(
    manager: TaskWorkspaceManager,
    role: RoleConfig,
    base: str,
    task_id: str,
    *,
    workspace_generation: int = 1,
):
    return manager.prepare(
        role=role,
        workflow_run_id="run-1",
        task_id=task_id,
        task_map_generation="map-1",
        workspace_generation=workspace_generation,
        base_ref=base,
    )


def test_task_workspaces_isolate_worker_reuse_and_restore_rework(
    tmp_path: Path,
) -> None:
    manager, role, base = _manager(tmp_path)
    task_a = _prepare(manager, role, base, "TASK-A")
    path_a = Path(task_a.project_path)
    (path_a / "a.txt").write_text("accepted A\n", encoding="utf-8")
    _git(path_a, "add", "a.txt")
    _git(path_a, "commit", "-q", "-m", "task A")
    accepted_a = _git(path_a, "rev-parse", "HEAD")

    task_c = _prepare(manager, role, base, "TASK-C")
    path_c = Path(task_c.project_path)
    (path_c / "c-dirty.txt").write_text("dirty C\n", encoding="utf-8")

    resumed_a = _prepare(manager, role, base, "TASK-A")

    assert resumed_a.project_path == task_a.project_path
    assert _git(path_a, "rev-parse", "HEAD") == accepted_a
    assert (path_a / "a.txt").read_text(encoding="utf-8") == "accepted A\n"
    assert not (path_a / "c-dirty.txt").exists()
    assert task_c.project_path != task_a.project_path


def test_task_workspace_setup_consumes_declared_identity_without_losing_candidate(tmp_path):
    manager, role, base = _manager(tmp_path)
    workspace = _prepare(manager, role, base, "TASK-A")
    project = Path(workspace.project_path)
    (project / "candidate.txt").write_text("keep uncommitted work")
    manifest = project / "dataset.identity.json"
    manifest.write_text('{"version":1}')
    manager.config.project.setup_script = "echo prepared >> preparation.log"
    manager.config.project.setup_inputs = ["dataset.identity.json"]
    _prepare(manager, role, base, "TASK-A")
    _prepare(manager, role, base, "TASK-A")
    assert (project / "preparation.log").read_text().splitlines() == ["prepared"]
    manifest.write_text('{"version":2}')
    _prepare(manager, role, base, "TASK-A")
    assert (project / "preparation.log").read_text().splitlines() == ["prepared", "prepared"]
    assert (project / "candidate.txt").read_text() == "keep uncommitted work"
    manifest.unlink()
    with pytest.raises(TaskWorkspaceError, match="declared setup input unavailable"):
        _prepare(manager, role, base, "TASK-A")


def test_existing_task_workspace_keeps_frozen_base_when_candidate_advances(
    tmp_path: Path,
) -> None:
    manager, role, base = _manager(tmp_path)
    task = _prepare(manager, role, base, "TASK-A")
    task_path = Path(task.project_path)
    (task_path / "task.txt").write_text("task work\n", encoding="utf-8")
    _git(task_path, "add", "task.txt")
    _git(task_path, "commit", "-q", "-m", "task work")
    task_head = _git(task_path, "rev-parse", "HEAD")

    (tmp_path / "candidate.txt").write_text("candidate work\n", encoding="utf-8")
    _git(tmp_path, "add", "candidate.txt")
    _git(tmp_path, "commit", "-q", "-m", "advance candidate")
    advanced_candidate = _git(tmp_path, "rev-parse", "HEAD")

    resumed = _prepare(manager, role, advanced_candidate, "TASK-A")

    assert resumed.project_path == task.project_path
    assert resumed.base_commit == base
    assert _git(task_path, "rev-parse", "HEAD") == task_head


def test_task_workspace_generation_and_base_are_currentness_fences(
    tmp_path: Path,
) -> None:
    manager, role, base = _manager(tmp_path)
    first = _prepare(manager, role, base, "TASK-A")
    second = _prepare(
        manager, role, base, "TASK-A", workspace_generation=2
    )

    assert first.project_path != second.project_path
    with pytest.raises(TaskWorkspaceError, match="base ref not found"):
        _prepare(manager, role, "missing-base", "TASK-B")


def test_task_workspace_cleanup_requires_settlement_and_clean_currentness(
    tmp_path: Path,
) -> None:
    manager, role, base = _manager(tmp_path)
    plan = _prepare(manager, role, base, "TASK-A")
    project_path = Path(plan.project_path)

    active = manager.cleanup(
        plan,
        task_terminal=True,
        integrated_or_archived=True,
        active_attempts=1,
        active_sessions=0,
    )
    assert active.status == "blocked"
    assert active.reason == "active_attempts"

    (project_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    dirty = manager.cleanup(
        plan,
        task_terminal=True,
        integrated_or_archived=True,
        active_attempts=0,
        active_sessions=0,
    )
    assert dirty.status == "dirty"

    (project_path / "dirty.txt").unlink()
    removed = manager.cleanup(
        plan,
        task_terminal=True,
        integrated_or_archived=True,
        active_attempts=0,
        active_sessions=0,
    )
    assert removed.removed
    assert not project_path.exists()
