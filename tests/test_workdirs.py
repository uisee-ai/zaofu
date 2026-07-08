"""Tests for workdir/git isolation roadmap foundation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    WorkdirConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.task_refs import TaskRefManager
from zf.runtime.workdirs import WorkdirManager


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _git_output(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _init_repo(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    return _git_output(root, "rev-parse", "HEAD")


def test_loader_parses_role_kind_and_runtime_workdir_config(tmp_path: Path):
    path = tmp_path / "zf.yaml"
    path.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n"
        "    role_kind: writer\n"
        "runtime:\n"
        "  workdirs:\n"
        "    enabled: true\n"
        "    root: .zf/workdirs\n"
        "    mode: dry-run\n"
        "  git:\n"
        "    writer_branch_prefix: worker\n"
        "    task_ref_prefix: task\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.roles[0].role_kind == "writer"
    assert config.runtime.workdirs.enabled is True
    assert config.runtime.workdirs.root == ".zf/workdirs"
    assert config.runtime.git.writer_branch_prefix == "worker"


def test_loader_rejects_invalid_role_kind(tmp_path: Path):
    path = tmp_path / "zf.yaml"
    path.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    role_kind: admin\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(path)


def test_workdir_manager_dry_run_writes_owner_marker_and_meta(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, root=".zf/workdirs"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )

    plan = manager.prepare(config.roles[0])

    workdir = Path(plan.workdir)
    assert (workdir / ".zf-workdir-owner.json").is_file()
    meta = json.loads((workdir / "meta.json").read_text(encoding="utf-8"))
    assert meta["instance_id"] == "dev"
    assert meta["branch_or_ref"] == "worker/dev"
    assert meta["git_worktree_created"] is False
    assert not (workdir / "project").exists()
    assert manager.doctor() == []


def test_workdir_doctor_reports_missing_meta(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, root=".zf/workdirs"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    plan = manager.prepare(config.roles[0])
    (Path(plan.workdir) / "meta.json").unlink()

    issues = manager.doctor()

    assert "dev: missing meta.json" in issues


def test_workdir_repair_refuses_unowned_directory(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "workdirs" / "dev").mkdir(parents=True)
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(workdirs=WorkdirConfig(enabled=True)),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )

    with pytest.raises(Exception):
        manager.repair("dev")


def test_workdir_repair_restores_missing_meta(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(workdirs=WorkdirConfig(enabled=True)),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    plan = manager.prepare(config.roles[0])
    meta_path = Path(plan.workdir) / "meta.json"
    meta_path.unlink()

    actions = manager.repair("dev")

    assert meta_path.exists()
    assert any("restored meta.json" in action for action in actions)


def test_default_workdir_root_follows_non_default_state_dir(tmp_path: Path):
    state_dir = tmp_path / "runtime-state"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir="runtime-state"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(workdirs=WorkdirConfig(enabled=True)),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )

    plan = manager.prepare(config.roles[0])

    workdir = state_dir / "workdirs" / "dev"
    assert Path(plan.workdir) == workdir
    assert (workdir / ".zf-workdir-owner.json").is_file()


def test_workdir_manager_worktree_mode_creates_writer_worktree(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )

    plan = manager.prepare(config.roles[0])

    project_path = Path(plan.project_path)
    assert (project_path / ".git").exists()
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=project_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    meta = json.loads((Path(plan.workdir) / "meta.json").read_text(encoding="utf-8"))
    assert branch == "worker/dev"
    assert meta["git_worktree_created"] is True
    assert meta["source_ref"]


def test_workdir_manager_worktree_mode_reuses_owned_worktree(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )

    first = manager.prepare(config.roles[0])
    second = manager.prepare(config.roles[0])

    assert first.project_path == second.project_path
    assert (Path(second.project_path) / ".git").exists()


def test_writer_worktree_syncs_clean_stale_branch_to_source_ref(tmp_path: Path):
    old_head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    plan = manager.prepare(config.roles[0])
    project_path = Path(plan.project_path)

    (tmp_path / "README.md").write_text("main baseline\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "advance main")
    new_head = _git_output(tmp_path, "rev-parse", "HEAD")

    result = manager.sync_writer_to_source_ref(config.roles[0])

    assert result["synced"] == "true"
    assert result["before"] == old_head
    assert result["after"] == new_head
    assert _git_output(project_path, "rev-parse", "HEAD") == new_head
    assert _git_output(project_path, "rev-parse", "--abbrev-ref", "HEAD") == "worker/dev"
    assert _git_output(project_path, "rev-parse", result["backup_ref"]) == old_head
    assert _git_output(project_path, "status", "--porcelain") == ""


def test_writer_worktree_sync_can_target_rework_task_ref(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    plan = manager.prepare(config.roles[0])
    project_path = Path(plan.project_path)
    (project_path / "task.ts").write_text("task implementation\n", encoding="utf-8")
    _git(project_path, "add", "task.ts")
    _git(project_path, "commit", "-q", "-m", "task implementation")
    task_head = _git_output(project_path, "rev-parse", "HEAD")

    (tmp_path / "README.md").write_text("main moved\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "advance main")
    main_head = _git_output(tmp_path, "rev-parse", "HEAD")
    _git(project_path, "reset", "--hard", main_head)

    result = manager.sync_writer_to_source_ref(
        config.roles[0],
        source_ref_override=task_head,
    )

    assert result["synced"] == "true"
    assert result["source_ref"] == task_head
    assert result["before"] == main_head
    assert result["after"] == task_head
    assert _git_output(project_path, "rev-parse", "HEAD") == task_head


def test_writer_worktree_applies_dependency_task_refs(tmp_path: Path):
    base_head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    plan = manager.prepare(config.roles[0])
    project_path = Path(plan.project_path)
    main_branch = _git_output(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-q", "-b", "task/TASK-A")
    (tmp_path / "dep.txt").write_text("from dependency\n", encoding="utf-8")
    _git(tmp_path, "add", "dep.txt")
    _git(tmp_path, "commit", "-q", "-m", "dependency task")
    dep_commit = _git_output(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", main_branch)
    refs_dir = state_dir / "refs"
    refs_dir.mkdir()
    (refs_dir / "task-index.json").write_text(json.dumps({
        "TASK-A": {
            "task_id": "TASK-A",
            "task_ref": "task/TASK-A",
            "source_commit": dep_commit,
        }
    }), encoding="utf-8")

    manager.sync_writer_to_source_ref(config.roles[0], source_ref_override=base_head)
    result = manager.apply_dependency_task_refs(config.roles[0], ["TASK-A"])

    assert (project_path / "dep.txt").read_text(encoding="utf-8") == "from dependency\n"
    assert result["before"] == base_head
    assert result["after"] == _git_output(project_path, "rev-parse", "HEAD")
    applied = result["applied_dependency_refs"]
    assert isinstance(applied, list)
    assert applied[0]["task_id"] == "TASK-A"
    assert applied[0]["source_commit"] == dep_commit
    assert _git_output(project_path, "status", "--porcelain") == ""
    meta = json.loads((Path(plan.workdir) / "meta.json").read_text(encoding="utf-8"))
    assert meta["dependency_refs"][0]["task_ref"] == "task/TASK-A"


def test_impl_child_completed_snapshots_changed_files_to_task_ref(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    workdirs = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    plan = workdirs.prepare(config.roles[0])
    project_path = Path(plan.project_path)
    (project_path / "data").mkdir()
    (project_path / "data" / "pulse.mjs").write_text(
        "export const statusItems = [];\n",
        encoding="utf-8",
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(ZfEvent(
        type="impl.child.completed",
        actor="dev",
        task_id="pulse-data-module",
        payload={
            "dispatch_id": "disp-1",
            "changed_files": ["data/pulse.mjs"],
            "evidence_refs": ["node --check data/pulse.mjs"],
        },
    ))

    assert result is not None
    assert result.status == "updated"
    payload = result.payload
    assert payload["task_ref"] == "task/pulse-data-module"
    source_commit = payload["source_commit"]
    assert source_commit == _git_output(
        tmp_path,
        "rev-parse",
        "refs/heads/task/pulse-data-module",
    )
    assert _git_output(
        tmp_path,
        "show",
        f"{source_commit}:data/pulse.mjs",
    ) == "export const statusItems = [];"
    index = json.loads((state_dir / "refs" / "task-index.json").read_text(
        encoding="utf-8",
    ))
    assert index["pulse-data-module"]["trigger_event_id"] == payload["trigger_event_id"]


def test_task_ref_scope_uses_dependency_after_from_writer_workdir(tmp_path: Path):
    base_head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="data-module",
        title="data module",
        status="done",
        contract=TaskContract(scope=["data.mjs"]),
    ))
    store.add(Task(
        id="http-server",
        title="http server",
        status="in_progress",
        assigned_to="dev",
        blocked_by=["data-module"],
        contract=TaskContract(scope=["server.mjs"]),
    ))
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    plan = manager.prepare(config.roles[0])
    project_path = Path(plan.project_path)
    main_branch = _git_output(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-q", "-b", "task/data-module")
    (tmp_path / "data.mjs").write_text(
        "export const statuses = ['green'];\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "data.mjs")
    _git(tmp_path, "commit", "-q", "-m", "data task")
    data_ref_commit = _git_output(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", main_branch)
    refs_dir = state_dir / "refs"
    refs_dir.mkdir()
    (refs_dir / "task-index.json").write_text(json.dumps({
        "data-module": {
            "task_id": "data-module",
            "task_ref": "task/data-module",
            "source_commit": data_ref_commit,
        }
    }), encoding="utf-8")

    manager.sync_writer_to_source_ref(config.roles[0], source_ref_override=base_head)
    dependency_result = manager.apply_dependency_task_refs(config.roles[0], ["data-module"])
    dependency_after = str(dependency_result["after"])
    if dependency_after == data_ref_commit:
        _git(
            project_path,
            "-c",
            "user.email=worker@example.com",
            "-c",
            "user.name=Worker",
            "commit",
            "--amend",
            "-q",
            "--no-edit",
        )
        dependency_after = _git_output(project_path, "rev-parse", "HEAD")
        meta_path = Path(plan.workdir) / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["dependency_after"] = dependency_after
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    (project_path / "server.mjs").write_text(
        "import { statuses } from './data.mjs';\n"
        "export function render() { return statuses.join(','); }\n",
        encoding="utf-8",
    )
    _git(project_path, "add", "server.mjs")
    _git(project_path, "commit", "-q", "-m", "server task")
    server_commit = _git_output(project_path, "rev-parse", "HEAD")

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(ZfEvent(
        type="impl.child.completed",
        actor="dev",
        task_id="http-server",
        payload={
            "dispatch_id": "disp-server",
            "source_commit": server_commit,
            "source_branch": "worker/dev",
            "workdir": str(project_path),
            "changed_files": ["server.mjs"],
            "files_touched": ["server.mjs"],
        },
    ))

    assert dependency_after != data_ref_commit
    assert result is not None
    assert result.status == "updated"
    assert result.payload["changed_files"] == ["server.mjs"]
    assert _git_output(
        tmp_path,
        "rev-parse",
        "refs/heads/task/http-server",
    ) == server_commit


def test_writer_worktree_dependency_ref_missing_fails_closed(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    manager.prepare(config.roles[0])

    with pytest.raises(RuntimeError, match="missing dependency task ref task/TASK-A"):
        manager.apply_dependency_task_refs(config.roles[0], ["TASK-A"])


def test_writer_worktree_sync_stashes_dirty_then_syncs(tmp_path: Path):
    """Dirty writer worktree no longer blocks dispatch — uncommitted content
    is stashed to ``refs/zf/workdir-stash/...`` so it can be recovered, then
    the regular reset-to-source-ref sync proceeds."""
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    plan = manager.prepare(config.roles[0])
    project_path = Path(plan.project_path)
    (tmp_path / "README.md").write_text("main baseline\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "advance main")
    new_head = _git_output(tmp_path, "rev-parse", "HEAD")
    (project_path / "README.md").write_text("dirty worker\n", encoding="utf-8")
    (project_path / "stash-only.md").write_text("untracked\n", encoding="utf-8")

    result = manager.sync_writer_to_source_ref(config.roles[0])

    assert result["synced"] == "true"
    assert result["source_ref"] == new_head
    assert result["after"] == new_head
    assert _git_output(project_path, "rev-parse", "HEAD") == new_head
    # Dirty content + untracked file went into the stash ref
    stash_ref = result["stashed_ref"]
    assert stash_ref.startswith("refs/zf/workdir-stash/dev/")
    stash_commit = _git_output(project_path, "rev-parse", stash_ref)
    blob = _git_output(project_path, "show", f"{stash_commit}:README.md")
    assert blob.rstrip("\n") == "dirty worker"
    untracked = _git_output(project_path, "show", f"{stash_commit}:stash-only.md")
    assert untracked.rstrip("\n") == "untracked"
    # Backup ref for the prior HEAD also recorded so operator can rollback
    assert result["backup_ref"].startswith("refs/zf/workdir-backups/dev/")


def test_writer_worktree_repair_switches_clean_task_branch_back_to_worker(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    plan = manager.prepare(config.roles[0])
    project_path = Path(plan.project_path)
    _git(project_path, "checkout", "-q", "-b", "task/TASK-1")

    manager.prepare(config.roles[0])

    assert _git_output(project_path, "rev-parse", "--abbrev-ref", "HEAD") == "worker/dev"


def test_writer_worktree_repair_stashes_dirty_wrong_branch(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    plan = manager.prepare(config.roles[0])
    project_path = Path(plan.project_path)
    _git(project_path, "checkout", "-q", "-b", "task/TASK-1")
    (project_path / "README.md").write_text("dirty\n", encoding="utf-8")
    (project_path / "untracked.txt").write_text("keep me\n", encoding="utf-8")

    manager.prepare(config.roles[0])

    assert _git_output(project_path, "rev-parse", "--abbrev-ref", "HEAD") == "worker/dev"
    assert _git_output(project_path, "status", "--porcelain") == ""
    refs = _git_output(tmp_path, "for-each-ref", "--format=%(refname)", "refs/zf/workdir-stash/dev")
    stash_ref = refs.splitlines()[-1]
    assert stash_ref.startswith("refs/zf/workdir-stash/dev/")
    stash_commit = _git_output(tmp_path, "rev-parse", stash_ref)
    assert _git_output(tmp_path, "show", f"{stash_commit}:README.md").rstrip("\n") == "dirty"
    assert _git_output(tmp_path, "show", f"{stash_commit}:untracked.txt").rstrip("\n") == "keep me"


def test_reader_worktree_checkouts_task_ref_detached(tmp_path: Path):
    head = _init_repo(tmp_path)
    _git(tmp_path, "branch", "task/TASK-1", head)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="review", backend="mock", role_kind="reader")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )

    target_ref = manager.checkout_reader_task_ref(config.roles[0], "TASK-1")

    project_path = state_dir / "workdirs" / "review" / "project"
    assert target_ref == "task/TASK-1"
    assert _git_output(project_path, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert _git_output(project_path, "rev-parse", "HEAD") == head


def test_reader_worktree_checkout_accepts_head_ref(tmp_path: Path):
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="review", backend="mock", role_kind="reader")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )

    target_ref = manager.checkout_reader_ref(config.roles[0], "HEAD")

    project_path = state_dir / "workdirs" / "review" / "project"
    assert target_ref == "HEAD"
    assert _git_output(project_path, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert _git_output(project_path, "rev-parse", "HEAD") == head


def test_reader_dirty_tree_is_reported_and_reset(tmp_path: Path):
    head = _init_repo(tmp_path)
    _git(tmp_path, "branch", "task/TASK-1", head)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="review", backend="mock", role_kind="reader")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    manager.checkout_reader_task_ref(config.roles[0], "TASK-1")
    project_path = state_dir / "workdirs" / "review" / "project"
    (project_path / "README.md").write_text("changed\n", encoding="utf-8")

    status = manager.reset_reader_if_dirty(config.roles[0])

    assert "README.md" in status
    assert (project_path / "README.md").read_text(encoding="utf-8") == "hello\n"


def test_reader_codex_hooks_json_is_runtime_projection_not_dirty(
    tmp_path: Path,
) -> None:
    head = _init_repo(tmp_path)
    _git(tmp_path, "branch", "task/TASK-1", head)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="review", backend="codex", role_kind="reader")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    manager.checkout_reader_task_ref(config.roles[0], "TASK-1")
    project_path = state_dir / "workdirs" / "review" / "project"
    hooks_path = project_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text('{"hooks":[]}\n', encoding="utf-8")

    status = manager.reset_reader_if_dirty(config.roles[0])

    assert status == ""
    assert hooks_path.exists()
    assert not any("dirty worktree" in issue for issue in manager.doctor())


def test_reader_checkout_cleans_dirty_tree_before_missing_ref_error(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="review", backend="mock", role_kind="reader")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    manager.prepare(config.roles[0])
    project_path = state_dir / "workdirs" / "review" / "project"
    (project_path / "README.md").write_text("stale reader edit\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="refs/heads/task/TASK-MISSING"):
        manager.checkout_reader_task_ref(config.roles[0], "TASK-MISSING")

    assert (project_path / "README.md").read_text(encoding="utf-8") == "hello\n"


def test_workdir_manager_worktree_mode_rejects_non_git_project(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )

    with pytest.raises(RuntimeError, match="requires .* git repo"):
        manager.prepare(config.roles[0])
    assert not (state_dir / "workdirs" / "dev").exists()


def test_workdir_manager_rejects_root_outside_state_dir(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, root="outside-workdirs"),
        ),
    )

    with pytest.raises(ValueError):
        WorkdirManager(state_dir=state_dir, project_root=tmp_path, config=config)


def test_start_workdir_dry_run_prepares_runtime_dirs(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n"
        "    role_kind: writer\n"
        "runtime:\n"
        "  workdirs:\n"
        "    enabled: true\n"
        "    mode: dry-run\n",
        encoding="utf-8",
    )
    assert main(["init"]) == 0

    result = main(["start", "--dry-run"])

    assert result == 0
    workdir = tmp_path / ".zf" / "workdirs" / "dev"
    assert (workdir / ".zf-workdir-owner.json").is_file()
    assert (workdir / "meta.json").is_file()


def _reader_manager(tmp_path: Path) -> WorkdirManager:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(exist_ok=True)
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="review", backend="mock", role_kind="reader")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, root=".zf/workdirs"),
        ),
    )
    return WorkdirManager(state_dir=state_dir, project_root=tmp_path, config=config)


def test_reader_reportable_status_filters_compiled_python(tmp_path: Path):
    # B5: python verification gates (py_compile / unittest) write __pycache__/
    # *.pyc into a reader worktree; those build artifacts must not count as
    # reader writes (reader.write_violation / reset). A genuine product write
    # must still survive the filter.
    manager = _reader_manager(tmp_path)
    status = (
        "?? __pycache__/\n"
        "?? calc.cpython-312.pyc\n"
        "?? sub/__pycache__/mod.cpython-312.pyc\n"
        " M src/real_change.py\n"
    )

    out = manager._reader_reportable_status(tmp_path, status)

    assert "src/real_change.py" in out
    assert "__pycache__" not in out
    assert ".pyc" not in out


def test_reader_reportable_status_pure_pyc_is_clean(tmp_path: Path):
    # A reader worktree dirtied ONLY by compiled-python artifacts is clean.
    manager = _reader_manager(tmp_path)
    out = manager._reader_reportable_status(tmp_path, "?? __pycache__/\n?? a.pyc\n")
    assert out.strip() == ""


def test_reader_reportable_status_filters_tooling_runtime_artifacts(tmp_path: Path):
    manager = _reader_manager(tmp_path)
    status = (
        "?? .venv/\n"
        "?? .pytest_cache/\n"
        "?? .coverage\n"
        " M src/real_change.py\n"
    )

    out = manager._reader_reportable_status(tmp_path, status)

    assert "src/real_change.py" in out
    assert ".venv" not in out
    assert ".pytest_cache" not in out
    assert ".coverage" not in out


def test_worktree_mode_runs_declared_setup_and_fails_closed(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test", setup_script="touch setup-ran.flag"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    manager = WorkdirManager(state_dir=state_dir, project_root=tmp_path, config=config)

    plan = manager.prepare(config.roles[0])

    project_path = Path(plan.project_path)
    assert (project_path / "setup-ran.flag").exists()

    # 失败 fail-closed:声明的 setup 挂了 → 铸造失败,不产可派发 workdir
    failing = ZfConfig(
        project=ProjectConfig(name="test", setup_script="exit 7"),
        roles=[RoleConfig(name="dev2", backend="mock", role_kind="writer")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    failing_manager = WorkdirManager(
        state_dir=state_dir, project_root=tmp_path, config=failing,
    )
    with pytest.raises(RuntimeError, match="workdir setup failed for dev2"):
        failing_manager.prepare(failing.roles[0])
