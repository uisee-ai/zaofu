from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    WorkdirConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_refs import TaskRefManager
from zf.runtime.workdirs import WorkdirManager

_SHA = "a" * 64


def _write_artifact(root: Path, path: str, text: str) -> str:
    artifact = root / path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(text, encoding="utf-8")
    return hashlib.sha256(artifact.read_bytes()).hexdigest()


class _StubTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str]] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    head = _git(root, "rev-parse", "HEAD")
    _git(root, "branch", "worker/dev", "HEAD")
    return head


def _config(state_dir: Path) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
            RoleConfig(name="review", backend="mock", triggers=["dev.build.done"]),
        ],
    )


def test_task_ref_manager_creates_ref_and_index_for_valid_build(tmp_path: Path):
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = _config(state_dir)
    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"source_commit": head, "source_branch": "worker/dev"},
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "rev-parse", "refs/heads/task/TASK-1") == head
    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    assert index["TASK-1"]["source_commit"] == head
    assert index["TASK-1"]["task_ref"] == "task/TASK-1"


def test_task_ref_manager_rejects_source_commit_outside_contract_scope(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="scoped task",
        status="in_progress",
        contract=TaskContract(scope=["src/task.py"]),
    ))
    base_branch = _git(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-q", "worker/dev")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "task.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "TODO.md").write_text("unrelated\n", encoding="utf-8")
    _git(tmp_path, "add", "src/task.py", "TODO.md")
    _git(tmp_path, "commit", "-q", "-m", "mixed task")
    source_commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", base_branch)

    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"source_commit": source_commit, "source_branch": "worker/dev"},
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "rejected"
    assert result.payload["reason"] == "source_commit changes outside task contract scope"
    assert result.payload["out_of_scope_files"] == ["TODO.md"]
    assert not (state_dir / "refs" / "task-index.json").exists()


def test_task_ref_manager_accepts_source_commit_inside_contract_scope(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="scoped task",
        status="in_progress",
        contract=TaskContract(scope=["src/task.py"]),
    ))
    base_branch = _git(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-q", "worker/dev")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "task.py").write_text("print('ok')\n", encoding="utf-8")
    _git(tmp_path, "add", "src/task.py")
    _git(tmp_path, "commit", "-q", "-m", "scoped task")
    source_commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", base_branch)

    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"source_commit": source_commit, "source_branch": "worker/dev"},
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "rev-parse", "refs/heads/task/TASK-1") == source_commit


def test_task_ref_manager_records_git_derived_changed_files_when_report_is_subset(
    tmp_path: Path,
) -> None:
    base = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    base_branch = _git(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-q", "worker/dev")
    (tmp_path / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    boot = tmp_path / "packages" / "assembly" / "src" / "boot.ts"
    boot.parent.mkdir(parents=True)
    boot.write_text("export const boot = true;\n", encoding="utf-8")
    _git(tmp_path, "add", "package.json", "packages/assembly/src/boot.ts")
    _git(tmp_path, "commit", "-q", "-m", "assembly root")
    source_commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", base_branch)

    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "source_commit": source_commit,
            "source_branch": "worker/dev",
            "base_git_head": base,
            "files_touched": ["package.json"],
        },
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "updated"
    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    entry = index["TASK-1"]
    assert set(entry["changed_files"]) == {
        "package.json",
        "packages/assembly/src/boot.ts",
    }
    assert entry["reported_files"] == ["package.json"]
    mismatch = entry["diagnostics"]["reported_files_mismatch"]
    assert mismatch["missing_from_report"] == ["packages/assembly/src/boot.ts"]


def test_task_ref_manager_accepts_scope_rooted_under_uniform_project_prefix(
    tmp_path: Path,
) -> None:
    # cj-min R23 regression (HIC-137BD6E031): contract scope globs authored
    # relative to a project sub-root while git reports repo-root-relative
    # paths. All changed files live inside the slice once the uniform leading
    # directory is stripped — a root-anchoring mismatch, not contamination.
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="scoped task",
        status="in_progress",
        contract=TaskContract(scope=["packages/gateway/**", "tests/gateway/**"]),
    ))
    base_branch = _git(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-q", "worker/dev")
    for path in (
        "cj-min/packages/gateway/src/command.ts",
        "cj-min/tests/gateway/command.test.ts",
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// slice\n", encoding="utf-8")
        _git(tmp_path, "add", path)
    _git(tmp_path, "commit", "-q", "-m", "gateway slice")
    source_commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", base_branch)

    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"source_commit": source_commit, "source_branch": "worker/dev"},
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "rev-parse", "refs/heads/task/TASK-1") == source_commit


def test_task_ref_manager_scopes_diff_from_nearest_accepted_task_ref(
    tmp_path: Path,
) -> None:
    """Shared lane branch: prior accepted handoffs are not this task's changes."""
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-0",
        title="legacy task",
        status="in_progress",
        contract=TaskContract(scope=["legacy/**"]),
    ))
    store.add(Task(
        id="TASK-1",
        title="scoped task",
        status="in_progress",
        contract=TaskContract(scope=["src/task.py"]),
    ))
    manager = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    )
    base_branch = _git(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-q", "worker/dev")
    (tmp_path / "legacy").mkdir()
    (tmp_path / "legacy" / "old.txt").write_text("prior round\n", encoding="utf-8")
    _git(tmp_path, "add", "legacy/old.txt")
    _git(tmp_path, "commit", "-q", "-m", "prior accepted task")
    prior_commit = _git(tmp_path, "rev-parse", "HEAD")
    accepted = manager.process_dev_build_done(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-0",
        payload={"source_commit": prior_commit, "source_branch": "worker/dev"},
    ))
    assert accepted is not None and accepted.status == "updated"
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "task.py").write_text("print('ok')\n", encoding="utf-8")
    _git(tmp_path, "add", "src/task.py")
    _git(tmp_path, "commit", "-q", "-m", "scoped task on shared branch")
    source_commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", base_branch)

    result = manager.process_dev_build_done(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"source_commit": source_commit, "source_branch": "worker/dev"},
    ))

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "rev-parse", "refs/heads/task/TASK-1") == source_commit


def test_task_ref_manager_rejects_cross_slice_leak_under_uniform_prefix(
    tmp_path: Path,
) -> None:
    # The root-rebase escape hatch must not weaken cross-slice protection:
    # with the same uniform prefix, a file outside the slice still rejects.
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="scoped task",
        status="in_progress",
        contract=TaskContract(scope=["packages/gateway/**", "tests/gateway/**"]),
    ))
    base_branch = _git(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-q", "worker/dev")
    for path in (
        "cj-min/packages/gateway/src/command.ts",
        "cj-min/packages/agent/leak.ts",
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// slice\n", encoding="utf-8")
        _git(tmp_path, "add", path)
    _git(tmp_path, "commit", "-q", "-m", "gateway slice with leak")
    source_commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", base_branch)

    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"source_commit": source_commit, "source_branch": "worker/dev"},
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "rejected"
    assert result.payload["reason"] == "source_commit changes outside task contract scope"
    assert "cj-min/packages/agent/leak.ts" in result.payload["out_of_scope_files"]


def test_task_ref_manager_preserves_legacy_build_events(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    event = ZfEvent(type="dev.build.done", actor="dev", task_id="TASK-1")

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "legacy"
    assert not (state_dir / "refs" / "task-index.json").exists()


def test_task_ref_manager_rejects_missing_git_handoff_in_worktree_mode(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    event = ZfEvent(type="dev.build.done", actor="dev", task_id="TASK-1")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
            RoleConfig(name="review", backend="mock", triggers=["dev.build.done"]),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "rejected"
    assert result.payload["reason"] == "missing git handoff payload in worktree mode"
    assert not (state_dir / "refs" / "task-index.json").exists()


def test_task_ref_manager_ignores_git_evidence_refs_when_missing_handoff(
    tmp_path: Path,
) -> None:
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = state_dir / "workdirs" / "dev" / "project"
    workdir.parent.mkdir(parents=True)
    _git(tmp_path, "worktree", "add", str(workdir), "worker/dev")
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="scoped task",
        status="in_progress",
        contract=TaskContract(scope=["src/task.ts"]),
    ))
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
            RoleConfig(name="review", backend="mock", triggers=["dev.build.done"]),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "changed_files": [f"git:{head}"],
            "artifact_refs": [f"git:{head}"],
            "evidence_refs": [f"git:{head}", "branch:worker/dev"],
        },
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "rejected"
    assert result.payload["reason"] == "missing git handoff payload in worktree mode"
    assert "out_of_scope_files" not in result.payload
    assert not (state_dir / "refs" / "task-index.json").exists()


def test_task_ref_manager_snapshots_declared_dev_artifacts_without_commit(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = state_dir / "workdirs" / "dev" / "project"
    workdir.parent.mkdir(parents=True)
    _git(tmp_path, "worktree", "add", str(workdir), "worker/dev")
    (workdir / "README.md").write_text("changed\n", encoding="utf-8")
    new_file = workdir / "src" / "new.ts"
    new_file.parent.mkdir(parents=True)
    new_file.write_text("export const value = 1;\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
            RoleConfig(name="review", backend="mock", triggers=["dev.build.done"]),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "artifact_refs": ["README.md", "src/new.ts"],
            "changed_files": ["README.md", "src/new.ts"],
        },
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "updated"
    commit = _git(tmp_path, "rev-parse", "refs/heads/task/TASK-1")
    assert _git(tmp_path, "show", f"{commit}:README.md") == "changed"
    assert _git(tmp_path, "show", f"{commit}:src/new.ts") == "export const value = 1;"
    assert _git(workdir, "status", "--porcelain") == ""
    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    assert index["TASK-1"]["source_branch"] == "worker/dev"
    assert index["TASK-1"]["source_commit"] == commit


def test_task_ref_manager_rejects_dirty_worktree_handoff_flag(tmp_path: Path):
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
            RoleConfig(name="review", backend="mock", triggers=["dev.build.done"]),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "source_commit": head,
            "source_branch": "worker/dev",
            "worktree_dirty": True,
        },
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "rejected"
    assert result.payload["reason"] == (
        "worktree_dirty handoff is not allowed in worktree mode"
    )
    assert not (state_dir / "refs" / "task-index.json").exists()


def test_task_ref_manager_rejects_dirty_workdir_handoff(tmp_path: Path):
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = state_dir / "workdirs" / "dev" / "project"
    workdir.parent.mkdir(parents=True)
    _git(tmp_path, "worktree", "add", str(workdir), "worker/dev")
    (workdir / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
            RoleConfig(name="review", backend="mock", triggers=["dev.build.done"]),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "source_commit": head,
            "source_branch": "worker/dev",
            "workdir": str(workdir),
        },
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "rejected"
    assert "has uncommitted changes" in result.payload["reason"]
    assert not (state_dir / "refs" / "task-index.json").exists()


def test_task_ref_manager_accepts_ignorable_harness_dirty_handoff(
    tmp_path: Path,
) -> None:
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = state_dir / "workdirs" / "dev" / "project"
    workdir.parent.mkdir(parents=True)
    _git(tmp_path, "worktree", "add", str(workdir), "worker/dev")
    hooks = workdir / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text("{}\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
            RoleConfig(name="review", backend="mock", triggers=["dev.build.done"]),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "source_commit": head,
            "source_branch": "worker/dev",
            "workdir": str(workdir),
            "worktree_dirty": True,
            "dirty_files": [".codex/hooks.json"],
            "dirty_scope_note": "Untracked Codex/ZaoFu hook config only",
        },
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "rev-parse", "refs/heads/task/TASK-1") == head
    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    diagnostics = index["TASK-1"]["diagnostics"]
    assert diagnostics["ignored_dirty_files"] == [".codex/hooks.json"]
    assert "hook config" in diagnostics["dirty_scope_note"]


def test_task_ref_manager_ignores_undeclared_runtime_materialized_dirty(
    tmp_path: Path,
) -> None:
    # ledger e2e 2026-06-20: dev workers committed real deliverables but left
    # the harness-materialized .claude/ skills dir untracked, and emitted
    # dev.build.done WITHOUT declaring worktree_dirty. The handoff must still
    # accept the ref — .claude/ is never a deliverable — instead of looping on
    # integration.failed via task.ref.rejected.
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = state_dir / "workdirs" / "dev" / "project"
    workdir.parent.mkdir(parents=True)
    _git(tmp_path, "worktree", "add", str(workdir), "worker/dev")
    skill = workdir / ".claude" / "skills" / "incremental-implementation" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# materialized skill\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
            RoleConfig(name="review", backend="mock", triggers=["dev.build.done"]),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "source_commit": head,
            "source_branch": "worker/dev",
            "workdir": str(workdir),
        },
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "rev-parse", "refs/heads/task/TASK-1") == head
    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    diagnostics = index["TASK-1"]["diagnostics"]
    assert any(
        ref.startswith(".claude/")
        for ref in diagnostics["ignored_runtime_dirty_files"]
    )


def test_task_ref_manager_rejects_non_head_source_commit(tmp_path: Path):
    old_head = _init_repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "worker/dev")
    (tmp_path / "later.txt").write_text("later\n", encoding="utf-8")
    _git(tmp_path, "add", "later.txt")
    _git(tmp_path, "commit", "-q", "-m", "later")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
            RoleConfig(name="review", backend="mock", triggers=["dev.build.done"]),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "source_commit": old_head,
            "source_branch": "worker/dev",
        },
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "rejected"
    assert "is not HEAD of worker/dev" in result.payload["reason"]
    assert not (state_dir / "refs" / "task-index.json").exists()


def test_task_ref_manager_accepts_rewound_branch_committed_handoff(
    tmp_path: Path,
) -> None:
    base = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="scoped task",
        status="in_progress",
        contract=TaskContract(scope=["src/task.py"]),
    ))
    base_branch = _git(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-q", "worker/dev")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "task.py").write_text("print('ok')\n", encoding="utf-8")
    _git(tmp_path, "add", "src/task.py")
    _git(tmp_path, "commit", "-q", "-m", "scoped task")
    source_commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", base_branch)
    _git(tmp_path, "update-ref", "refs/heads/worker/dev", base)
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
            RoleConfig(name="review", backend="mock", triggers=["dev.build.done"]),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "source_commit": source_commit,
            "source_branch": "worker/dev",
            "base_git_head": base,
            "changed_files": ["src/task.py"],
        },
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "rev-parse", "refs/heads/task/TASK-1") == source_commit
    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    mismatch = index["TASK-1"]["diagnostics"]["source_branch_head_mismatch"]
    assert mismatch["branch_head"] == base
    assert mismatch["source_commit"] == source_commit


def test_arch_proposal_snapshots_artifacts_for_reader_checkout(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = state_dir / "workdirs" / "arch" / "project"
    workdir.parent.mkdir(parents=True)
    _git(tmp_path, "worktree", "add", "--detach", str(workdir), "HEAD")
    artifact = workdir / "docs" / "plans" / "plan.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("arch plan\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="arch", backend="mock", role_kind="reader"),
            RoleConfig(name="critic", backend="mock", role_kind="reader"),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_arch_proposal_done(ZfEvent(
        type="arch.proposal.done",
        actor="arch",
        task_id="TASK-1",
        payload={
            "artifact_refs": ["docs/plans/plan.md"],
            "file_plan": ["docs/plans/plan.md"],
        },
    ))

    assert result is not None
    assert result.status == "updated"
    commit = _git(tmp_path, "rev-parse", "refs/heads/task/TASK-1")
    assert _git(tmp_path, "show", f"{commit}:docs/plans/plan.md") == "arch plan"
    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    assert index["TASK-1"]["task_ref"] == "task/TASK-1"

    WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).checkout_reader_task_ref(config.roles[1], "TASK-1")
    critic_plan = state_dir / "workdirs" / "critic" / "project" / "docs" / "plans" / "plan.md"
    assert critic_plan.read_text(encoding="utf-8") == "arch plan\n"


def test_arch_proposal_missing_artifacts_publishes_base_ref(tmp_path: Path):
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = state_dir / "workdirs" / "arch" / "project"
    workdir.parent.mkdir(parents=True)
    _git(tmp_path, "worktree", "add", "--detach", str(workdir), "HEAD")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="arch", backend="mock", role_kind="reader"),
            RoleConfig(name="critic", backend="mock", role_kind="reader"),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_arch_proposal_done(ZfEvent(
        type="arch.proposal.done",
        actor="arch",
        task_id="TASK-1",
        payload={
            "artifact_refs": ["docs/records/future.md"],
            "file_plan": ["docs/records/future.md"],
        },
    ))

    assert result is not None
    assert result.status == "updated"
    assert result.payload["source_commit"] == head
    assert result.payload["missing_artifact_refs"] == ["docs/records/future.md"]
    assert _git(tmp_path, "rev-parse", "refs/heads/task/TASK-1") == head


def test_arch_proposal_artifacts_skip_git_ref_when_workdirs_disabled(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="arch", backend="mock", role_kind="reader"),
            RoleConfig(name="critic", backend="mock", role_kind="reader"),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=False, mode="dry-run"),
        ),
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_arch_proposal_done(ZfEvent(
        id="evt-arch",
        type="arch.proposal.done",
        actor="arch",
        task_id="TASK-1",
        payload={
            "artifact_refs": ["docs/plans/plan.md"],
            "file_plan": ["docs/plans/plan.md"],
        },
    ))

    assert result is not None
    assert result.status == "legacy"
    assert result.payload["trigger_event_id"] == "evt-arch"
    assert result.payload["artifact_refs"] == ["docs/plans/plan.md"]
    assert not (state_dir / "refs" / "task-index.json").exists()


def test_arch_proposal_without_manifest_warns_on_detected_plan_artifacts(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir = state_dir / "workdirs" / "arch" / "project"
    workdir.parent.mkdir(parents=True)
    _git(tmp_path, "worktree", "add", "--detach", str(workdir), "HEAD")
    (workdir / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    tasks_dir = workdir / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "plan.md").write_text("# Plan\n", encoding="utf-8")

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_arch_proposal_done(ZfEvent(
        type="arch.proposal.done",
        actor="arch",
        task_id="TASK-1",
        payload={},
    ))

    assert result is not None
    assert result.status == "rejected"
    assert result.payload["fallback_warning"]
    assert result.payload["detected_artifacts"] == ["SPEC.md", "tasks/plan.md"]
    assert result.payload["required_action"] == (
        "emit artifact.manifest.published with accepted contract refs"
    )
    assert not (state_dir / "refs" / "task-index.json").exists()


def test_arch_proposal_without_manifest_emits_ref_rejection_diagnostic(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    workdir = state_dir / "workdirs" / "arch" / "project"
    workdir.parent.mkdir(parents=True)
    _git(tmp_path, "worktree", "add", "--detach", str(workdir), "HEAD")
    (workdir / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    tasks_dir = workdir / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "todo.md").write_text("# TODO\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="arch", backend="mock", role_kind="reader"),
            RoleConfig(name="critic", backend="mock", role_kind="reader"),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    event = ZfEvent(
        id="evt-arch",
        type="arch.proposal.done",
        actor="arch",
        task_id="TASK-1",
        payload={},
    )

    Orchestrator(
        state_dir,
        config,
        _StubTransport(),
    )._apply_housekeeping(event)  # type: ignore[attr-defined]

    events = EventLog(state_dir / "events.jsonl").read_all()
    rejected = next(e for e in events if e.type == "task.ref.rejected")
    assert rejected.payload["trigger_event_id"] == "evt-arch"
    assert rejected.payload["detected_artifacts"] == ["SPEC.md", "tasks/todo.md"]
    assert rejected.payload["required_action"] == (
        "emit artifact.manifest.published with accepted contract refs"
    )
    assert not (state_dir / "refs" / "task-index.json").exists()


def test_artifact_manifest_updates_task_and_feature_refs(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    spec_sha = _write_artifact(tmp_path, "docs/specs/task.md", "spec\n")
    plan_sha = _write_artifact(tmp_path, "docs/plans/task-plan.md", "plan\n")
    tdd_sha = _write_artifact(tmp_path, "docs/plans/task-tdd.md", "tdd\n")

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_artifact_manifest_published(ZfEvent(
        type="artifact.manifest.published",
        actor="arch",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "feature_id": "F-12345678",
            "role": "arch",
            "skills_used": ["agent-skills:design"],
            "artifact_refs": [
                {
                    "kind": "sdd",
                    "path": "docs/specs/task.md",
                    "sha256": spec_sha,
                    "summary": "spec",
                },
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": plan_sha,
                    "summary": "plan",
                },
                {
                    "kind": "tdd",
                    "path": "docs/plans/task-tdd.md",
                    "sha256": tdd_sha,
                    "summary": "tdd",
                },
            ],
            "handoff_contract": {"required_for_dev": ["spec", "plan", "tdd"]},
        },
    ))

    assert result is not None
    assert result.status == "updated"
    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    assert index["TASK-1"]["contract_refs"]["spec_ref"] == "docs/specs/task.md"
    assert index["TASK-1"]["contract_refs"]["plan_ref"] == "docs/plans/task-plan.md"
    assert index["TASK-1"]["contract_refs"]["tdd_ref"] == "docs/plans/task-tdd.md"
    assert {item["status"] for item in index["TASK-1"]["hash_status"]} == {"ok"}
    feature_index = json.loads((state_dir / "refs" / "feature-index.json").read_text())
    assert feature_index["F-12345678"]["tasks"]["TASK-1"]["contract_refs"]["tdd_ref"]
    assert feature_index["F-12345678"]["tasks"]["TASK-1"]["hash_status"][0]["status"] == "ok"


def test_artifact_manifest_projects_current_feature_delivery_bundle(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task_map_sha = _write_artifact(
        tmp_path,
        ".zf/artifacts/F-BUNDLE/v2/task_map.json",
        '{"schema_version":"task-map.v1","feature_id":"F-BUNDLE","tasks":[]}\n',
    )
    source_index_sha = _write_artifact(
        tmp_path,
        ".zf/artifacts/F-BUNDLE/v2/source_index.json",
        '{"schema_version":"source-index.v1","feature_id":"F-BUNDLE","tasks":[]}\n',
    )
    coverage_sha = _write_artifact(
        tmp_path,
        ".zf/artifacts/F-BUNDLE/v2/coverage_report.json",
        '{"schema_version":"coverage-report.v1","feature_id":"F-BUNDLE"}\n',
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_artifact_manifest_published(ZfEvent(
        id="evt-bundle",
        type="artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-BUNDLE",
        payload={
            "task_id": "TASK-BUNDLE",
            "feature_id": "F-BUNDLE",
            "role": "orchestrator",
            "artifact_refs": [
                {
                    "kind": "task_map",
                    "path": ".zf/artifacts/F-BUNDLE/v2/task_map.json",
                    "sha256": task_map_sha,
                    "summary": "task map",
                    "status": "accepted",
                    "version": 2,
                },
                {
                    "kind": "source_index",
                    "path": ".zf/artifacts/F-BUNDLE/v2/source_index.json",
                    "sha256": source_index_sha,
                    "summary": "source index",
                    "status": "accepted",
                    "version": 2,
                },
                {
                    "kind": "coverage_report",
                    "path": ".zf/artifacts/F-BUNDLE/v2/coverage_report.json",
                    "sha256": coverage_sha,
                    "summary": "coverage",
                    "status": "accepted",
                    "version": 2,
                },
            ],
        },
    ))

    assert result is not None and result.status == "updated"
    feature_index = json.loads((state_dir / "refs" / "feature-index.json").read_text())
    bundle = feature_index["F-BUNDLE"]["current_bundle"]
    assert bundle["schema_version"] == "feature-delivery-bundle.v1"
    assert bundle["current_task_map_ref"].endswith("v2/task_map.json")
    assert bundle["current_source_index_ref"].endswith("v2/source_index.json")
    assert bundle["current_coverage_report_ref"].endswith("v2/coverage_report.json")
    assert bundle["is_current"] is True
    assert feature_index["F-BUNDLE"]["current_task_map_ref"] == bundle["current_task_map_ref"]
    assert feature_index["F-BUNDLE"]["bundle_history"][-1]["is_current"] is True


def test_feature_delivery_bundle_prefers_later_artifact_when_version_ties(
    tmp_path: Path,
):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    manager = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    )
    v1_task_map_sha = _write_artifact(
        tmp_path,
        ".zf/artifacts/F-BUNDLE/v1/task_map.json",
        '{"schema_version":"task-map.v1","feature_id":"F-BUNDLE","tasks":[]}\n',
    )
    v1_source_index_sha = _write_artifact(
        tmp_path,
        ".zf/artifacts/F-BUNDLE/v1/source_index.json",
        '{"schema_version":"source-index.v1","feature_id":"F-BUNDLE","tasks":[]}\n',
    )
    v2_task_map_sha = _write_artifact(
        tmp_path,
        ".zf/artifacts/F-BUNDLE/v2/task_map.json",
        '{"schema_version":"task-map.v1","feature_id":"F-BUNDLE","tasks":[]}\n',
    )
    v2_source_index_sha = _write_artifact(
        tmp_path,
        ".zf/artifacts/F-BUNDLE/v2/source_index.json",
        '{"schema_version":"source-index.v1","feature_id":"F-BUNDLE","tasks":[]}\n',
    )

    for event_id, version_dir, task_map_sha, source_index_sha in (
        ("evt-bundle-v1", "v1", v1_task_map_sha, v1_source_index_sha),
        ("evt-bundle-v2", "v2", v2_task_map_sha, v2_source_index_sha),
    ):
        result = manager.process_artifact_manifest_published(ZfEvent(
            id=event_id,
            type="artifact.manifest.published",
            actor="orchestrator",
            task_id="TASK-BUNDLE",
            payload={
                "task_id": "TASK-BUNDLE",
                "feature_id": "F-BUNDLE",
                "role": "orchestrator",
                "artifact_refs": [
                    {
                        "kind": "task_map",
                        "path": f".zf/artifacts/F-BUNDLE/{version_dir}/task_map.json",
                        "sha256": task_map_sha,
                        "summary": f"{version_dir} task map",
                        "status": "accepted",
                        "version": 1,
                    },
                    {
                        "kind": "source_index",
                        "path": f".zf/artifacts/F-BUNDLE/{version_dir}/source_index.json",
                        "sha256": source_index_sha,
                        "summary": f"{version_dir} source index",
                        "status": "accepted",
                        "version": 1,
                    },
                ],
            },
        ))
        assert result is not None and result.status == "updated"

    feature_index = json.loads((state_dir / "refs" / "feature-index.json").read_text())
    bundle = feature_index["F-BUNDLE"]["current_bundle"]
    assert bundle["current_task_map_ref"].endswith("v2/task_map.json")
    assert bundle["current_source_index_ref"].endswith("v2/source_index.json")
    assert bundle["version"] == 2
    assert bundle["manifest_event_id"] == "evt-bundle-v2"
    assert feature_index["F-BUNDLE"]["bundle_history"][-1]["is_current"] is True
    task_index_path = state_dir / "refs" / "task-index.json"
    task_index = json.loads(task_index_path.read_text())
    for ref in task_index["TASK-BUNDLE"]["artifact_refs"]:
        if str(ref.get("path") or "").endswith("v2/task_map.json"):
            ref["status"] = "superseded"
    task_index_path.write_text(json.dumps(task_index), encoding="utf-8")

    replay = manager.process_artifact_manifest_published(ZfEvent(
        id="evt-bundle-v2",
        type="artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-BUNDLE",
        payload={
            "task_id": "TASK-BUNDLE",
            "feature_id": "F-BUNDLE",
            "role": "orchestrator",
            "artifact_refs": [
                {
                    "kind": "task_map",
                    "path": ".zf/artifacts/F-BUNDLE/v2/task_map.json",
                    "sha256": v2_task_map_sha,
                    "summary": "v2 task map",
                    "status": "accepted",
                    "version": 1,
                },
                {
                    "kind": "source_index",
                    "path": ".zf/artifacts/F-BUNDLE/v2/source_index.json",
                    "sha256": v2_source_index_sha,
                    "summary": "v2 source index",
                    "status": "accepted",
                    "version": 1,
                },
            ],
        },
    ))
    assert replay is not None and replay.status == "updated"
    task_index = json.loads(task_index_path.read_text())
    assert [
        ref["status"]
        for ref in task_index["TASK-BUNDLE"]["artifact_refs"]
        if str(ref.get("path") or "").endswith("v2/task_map.json")
    ] == ["accepted"]


def test_artifact_manifest_indexes_actor_role_and_semver_label(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    plan_sha = _write_artifact(tmp_path, "docs/plans/task-plan.md", "plan\n")

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_artifact_manifest_published(ZfEvent(
        id="evt-manifest",
        type="artifact.manifest.published",
        actor="arch",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": plan_sha,
                    "summary": "plan",
                    "version": "0.1.0",
                },
            ],
        },
    ))

    assert result is not None
    assert result.status == "updated"
    ref = result.payload["artifact_refs"][0]
    assert ref["version"] == 1
    assert ref["artifact_id"] == "plan-task-1-v1"


def test_artifact_manifest_hash_status_resolves_role_workdir_refs(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    workdir_project = state_dir / "workdirs" / "arch" / "project"
    plan_sha = _write_artifact(
        workdir_project,
        "docs/plans/task-plan.md",
        "plan from arch workdir\n",
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_artifact_manifest_published(ZfEvent(
        type="artifact.manifest.published",
        actor="arch",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": plan_sha,
                    "summary": "plan",
                    "workdir_path": str(workdir_project),
                },
            ],
        },
    ))

    assert result is not None and result.status == "updated"
    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    hash_status = index["TASK-1"]["hash_status"][0]
    assert hash_status["status"] == "ok"
    assert hash_status["resolved_path"] == str(workdir_project / "docs/plans/task-plan.md")


def test_artifact_manifest_index_versions_supersede_prior_refs(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    manager = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    )

    first = manager.process_artifact_manifest_published(ZfEvent(
        id="evt-v1",
        type="artifact.manifest.published",
        actor="arch",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": "b" * 64,
                    "summary": "plan v1",
                },
            ],
        },
    ))
    second = manager.process_artifact_manifest_published(ZfEvent(
        id="evt-v2",
        type="artifact.manifest.published",
        actor="arch",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": "c" * 64,
                    "summary": "plan v2",
                },
            ],
        },
    ))

    assert first is not None and first.status == "updated"
    assert second is not None and second.status == "updated"
    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    refs = index["TASK-1"]["artifact_refs_by_kind"]["plan"]
    assert refs[0]["artifact_id"] == "plan-task-1-v1"
    assert refs[0]["version"] == 1
    assert refs[0]["status"] == "superseded"
    assert refs[1]["artifact_id"] == "plan-task-1-v2"
    assert refs[1]["version"] == 2
    assert refs[1]["supersedes"] == "plan-task-1-v1"
    assert refs[1]["source_event_id"] == "evt-v2"


def test_artifact_manifest_proposed_ref_is_not_marked_accepted(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_artifact_manifest_published(ZfEvent(
        id="evt-proposed",
        type="artifact.manifest.published",
        actor="arch",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": "d" * 64,
                    "summary": "candidate plan",
                    "status": "proposed",
                },
            ],
        },
    ))

    assert result is not None and result.status == "updated"
    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    ref = index["TASK-1"]["artifact_refs"][0]
    assert ref["status"] == "proposed"
    assert ref["source_event_id"] == "evt-proposed"
    assert ref["accepted_event_id"] == ""
    assert "plan_ref" not in index["TASK-1"].get("contract_refs", {})
    assert (
        index["TASK-1"]["candidate_contract_refs"]["plan_ref"]
        == "docs/plans/task-plan.md"
    )


def test_artifact_manifest_rejects_invalid_payload(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_artifact_manifest_published(ZfEvent(
        type="artifact.manifest.published",
        actor="arch",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "sdd",
                    "path": "../secret.md",
                    "summary": "bad",
                },
            ],
        },
    ))

    assert result is not None
    assert result.status == "rejected"
    assert "sha256 is required" in result.payload["reason"]
    assert not (state_dir / "refs" / "task-index.json").exists()


def test_orchestrator_manifest_updates_contract_and_briefing_refs(
    tmp_path: Path,
):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    spec_sha = _write_artifact(tmp_path, "docs/specs/task.md", "spec\n")
    plan_sha = _write_artifact(tmp_path, "docs/plans/task-plan.md", "plan\n")
    tdd_sha = _write_artifact(tmp_path, "docs/plans/task-tdd.md", "tdd\n")
    critic_sha = _write_artifact(tmp_path, "docs/reviews/task-critic.md", "critic\n")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                dev_requires_orchestrator_backlog=True,
                required_backlog_refs=[
                    "spec_ref",
                    "plan_ref",
                    "tdd_ref",
                    "critic_event_id",
                    "critic_gate_ref",
                    "evidence_contract",
                ],
            ),
        ),
    )
    config.verification.contract.required = True
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-1",
        title="x",
        status="backlog",
        assigned_to="dev",
        contract=TaskContract(
            behavior="deliver x",
            verification="python -m pytest tests/test_x.py",
            verification_tiers=["runtime"],
            owner_role="dev",
            scope=["src/x.py"],
        ),
    ))
    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    task = store.get("TASK-1")
    assert task is not None
    assert any(
        "contract.spec_ref is required" in error
        for error in orch._strict_contract_preflight_errors(task)  # type: ignore[attr-defined]
    )

    published = orch.event_writer.append(ZfEvent(
        type="artifact.manifest.published",
        actor="critic",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "role": "critic",
            "artifact_refs": [
                {
                    "kind": "sdd",
                    "path": "docs/specs/task.md",
                    "sha256": spec_sha,
                    "summary": "spec",
                },
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": plan_sha,
                    "summary": "plan",
                },
                {
                    "kind": "tdd",
                    "path": "docs/plans/task-tdd.md",
                    "sha256": tdd_sha,
                    "summary": "tdd",
                },
                {
                    "kind": "critic_review",
                    "path": "docs/reviews/task-critic.md",
                    "sha256": critic_sha,
                    "summary": "critic approved",
                },
            ],
            "handoff_contract": {
                "required_for_dev": ["spec", "plan", "tdd"],
                "required_for_review": ["critic_review"],
            },
        },
    ))
    orch._apply_housekeeping(published)  # type: ignore[attr-defined]

    updated = TaskStore(state_dir / "kanban.json").get("TASK-1")
    assert updated is not None
    assert updated.contract.spec_ref == "docs/specs/task.md"
    assert updated.contract.plan_ref == "docs/plans/task-plan.md"
    assert updated.contract.tdd_ref == "docs/plans/task-tdd.md"
    assert updated.contract.critic_gate_ref == "docs/reviews/task-critic.md"
    assert updated.contract.critic_event_id == published.id
    assert updated.contract.evidence_contract["artifact_manifest_event_id"] == published.id
    assert orch._strict_contract_preflight_errors(updated) == []  # type: ignore[attr-defined]

    orch._dispatch_task(updated, config.roles[0])  # type: ignore[attr-defined]

    assert transport.sent
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "## Artifact Manifest Refs" in briefing
    assert "docs/specs/task.md" in briefing
    assert '"hash_status"' in briefing
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(event.type == "task.artifact_refs.updated" for event in events)
    assert any(event.type == "task.contract.update" for event in events)


def test_strict_dispatch_blocks_mismatched_accepted_artifact(
    tmp_path: Path,
):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_artifact(tmp_path, "docs/plans/task-plan.md", "changed\n")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
    )
    config.verification.contract.required = True
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-1",
        title="x",
        status="backlog",
        assigned_to="dev",
        contract=TaskContract(
            behavior="deliver x",
            verification="python -m pytest tests/test_x.py",
            verification_tiers=["runtime"],
            owner_role="dev",
            plan_ref="docs/plans/task-plan.md",
        ),
    ))
    manager = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    )
    manager.process_artifact_manifest_published(ZfEvent(
        type="artifact.manifest.published",
        actor="arch",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": "f" * 64,
                    "summary": "plan",
                },
            ],
        },
    ))
    orch = Orchestrator(state_dir, config, _StubTransport())  # type: ignore[arg-type]
    task = store.get("TASK-1")
    assert task is not None

    errors = orch._strict_contract_preflight_errors(task)  # type: ignore[attr-defined]

    assert any("hash verification failed" in error for error in errors)


def test_orchestrator_rejects_invalid_manifest_event(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = _config(state_dir)
    orch = Orchestrator(state_dir, config, _StubTransport())  # type: ignore[arg-type]

    published = orch.event_writer.append(ZfEvent(
        type="artifact.manifest.published",
        actor="arch",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "role": "arch",
            "artifact_refs": [
                {
                    "kind": "sdd",
                    "path": "docs/specs/task.md",
                    "summary": "missing sha",
                },
            ],
        },
    ))
    orch._apply_housekeeping(published)  # type: ignore[attr-defined]

    events = EventLog(state_dir / "events.jsonl").read_all()
    rejected = next(event for event in events if event.type == "artifact.manifest.rejected")
    assert rejected.payload["errors"] == ["artifact_refs[0].sha256 is required"]
    assert not (state_dir / "refs" / "task-index.json").exists()


def test_invalid_task_ref_blocks_legacy_layer1_handoff(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n")
    config = _config(state_dir)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-1", title="x", status="in_progress", assigned_to="dev"))
    orch = Orchestrator(state_dir, config, _StubTransport())  # type: ignore[arg-type]
    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"source_commit": "deadbeef", "source_branch": "worker/dev"},
    )

    orch._apply_housekeeping(event)  # type: ignore[attr-defined]
    decision = orch._on_build_done(event)  # type: ignore[attr-defined]

    assert decision is not None
    assert decision.action == "block"
    assert store.get("TASK-1").status == "in_progress"  # type: ignore[union-attr]
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(e.type == "task.ref.rejected" for e in events)


def test_arch_reader_missing_pre_task_ref_is_checkout_skip(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[RoleConfig(name="arch", backend="mock", role_kind="reader")],
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
    project_path = state_dir / "workdirs" / "arch" / "project"
    (project_path / "README.md").write_text("stale arch edit\n", encoding="utf-8")
    orch = Orchestrator(state_dir, config, _StubTransport())  # type: ignore[arg-type]

    target_ref = orch._checkout_reader_target_ref(config.roles[0], "TASK-NOREF")  # type: ignore[attr-defined]

    assert target_ref is None
    assert (project_path / "README.md").read_text(encoding="utf-8") == "hello\n"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(
        event.type == "reader.write_violation"
        and event.payload.get("trigger_event") == "dispatch_preflight"
        and event.payload.get("classification") == "tracked_source_mutation"
        for event in events
    )
    skipped = [event for event in events if event.type == "reader.checkout_skipped"]
    assert skipped
    assert skipped[-1].payload["classification"] == "pre_task_ref_unavailable"
    assert skipped[-1].payload["required"] is False


def test_task_ref_update_after_rejection_unblocks_same_build_event(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n")
    config = _config(state_dir)
    orch = Orchestrator(state_dir, config, _StubTransport())  # type: ignore[arg-type]
    build_event = ZfEvent(
        id="evt-build",
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
    )
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"trigger_event_id": "evt-build"},
    ))
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"trigger_event_id": "evt-build"},
    ))

    assert orch._task_ref_rejected(build_event) is False  # type: ignore[attr-defined]


def test_missing_task_ref_blocks_worktree_handoff_before_review(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
            RoleConfig(name="review", backend="mock", triggers=["dev.build.done"]),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-1", title="x", status="in_progress", assigned_to="dev"))
    orch = Orchestrator(state_dir, config, _StubTransport())  # type: ignore[arg-type]
    event = ZfEvent(type="dev.build.done", actor="dev", task_id="TASK-1")

    orch._apply_housekeeping(event)  # type: ignore[attr-defined]
    decision = orch._on_build_done(event)  # type: ignore[attr-defined]

    assert decision is not None
    assert decision.action == "block"
    assert store.get("TASK-1").status == "in_progress"  # type: ignore[union-attr]
    events = EventLog(state_dir / "events.jsonl").read_all()
    rejection = next(e for e in events if e.type == "task.ref.rejected")
    assert rejection.payload["reason"] == "missing git handoff payload in worktree mode"


def test_reader_terminal_event_reports_and_resets_dirty_worktree(tmp_path: Path):
    head = _init_repo(tmp_path)
    _git(tmp_path, "branch", "task/TASK-1", head)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(
                name="review",
                backend="mock",
                role_kind="reader",
                publishes=["review.approved", "review.rejected"],
            ),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-1", title="x", status="in_progress", assigned_to="review"))
    WorkdirManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).checkout_reader_task_ref(config.roles[0], "TASK-1")
    project_path = state_dir / "workdirs" / "review" / "project"
    (project_path / "README.md").write_text("changed\n", encoding="utf-8")
    orch = Orchestrator(state_dir, config, _StubTransport())  # type: ignore[arg-type]
    event = ZfEvent(type="review.approved", actor="review", task_id="TASK-1")

    orch._apply_housekeeping(event)  # type: ignore[attr-defined]

    assert (project_path / "README.md").read_text(encoding="utf-8") == "hello\n"
    events = EventLog(state_dir / "events.jsonl").read_all()
    violation = next(e for e in events if e.type == "reader.write_violation")
    assert violation.task_id == "TASK-1"
    assert violation.payload["instance_id"] == "review"
    assert violation.payload["reset"] is True
    assert violation.payload["classification"] == "tracked_source_mutation"
    assert violation.payload["has_tracked"] is True
    assert "README.md" in violation.payload["status"]


def test_reader_dispatch_briefing_includes_ref_and_source_commit(tmp_path: Path):
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", role_kind="writer"),
            RoleConfig(
                name="review",
                backend="mock",
                role_kind="reader",
                triggers=["dev.build.done"],
                publishes=["review.approved", "review.rejected"],
            ),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"source_commit": head, "source_branch": "worker/dev"},
    ))
    (tmp_path / "later.txt").write_text("main moved\n", encoding="utf-8")
    _git(tmp_path, "add", "later.txt")
    _git(tmp_path, "commit", "-q", "-m", "main moves")
    latest = _git(tmp_path, "rev-parse", "HEAD")
    assert latest != head
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-1", title="x", status="backlog"))
    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    orch._dispatch_heads["TASK-1"] = head  # type: ignore[attr-defined]
    task = store.get("TASK-1")
    assert task is not None

    orch._dispatch_task(task, config.roles[1])  # type: ignore[attr-defined]

    assert transport.sent
    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "target_ref: `task/TASK-1`" in briefing
    assert f"source_commit: `{head}`" in briefing
    assert "## Git Evidence Context" in briefing
    assert f"**HEAD**: `{head}`" in briefing
    assert f"**Base**: `{head}`" in briefing
    assert "TASK-1" in orch._dispatch_epoch  # type: ignore[attr-defined]
    assert orch._dispatch_heads["TASK-1"] == head  # type: ignore[attr-defined]
    events = EventLog(state_dir / "events.jsonl").read_all()
    dispatched = next(e for e in events if e.type == "task.dispatched")
    assert dispatched.payload["target_ref"] == "task/TASK-1"
    assert dispatched.payload["base_git_head"] == head


def test_reader_dispatch_git_evidence_uses_task_worktree_not_main(tmp_path: Path):
    base = _init_repo(tmp_path)
    _git(tmp_path, "branch", "-M", "main")
    _git(tmp_path, "checkout", "-q", "worker/dev")
    (tmp_path / "worker-file.txt").write_text("worker\n", encoding="utf-8")
    _git(tmp_path, "add", "worker-file.txt")
    _git(tmp_path, "commit", "-q", "-m", "task change")
    task_head = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "main-file.txt").write_text("main moved\n", encoding="utf-8")
    _git(tmp_path, "add", "main-file.txt")
    _git(tmp_path, "commit", "-q", "-m", "main moves")
    main_head = _git(tmp_path, "rev-parse", "HEAD")
    assert task_head != main_head

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", role_kind="writer"),
            RoleConfig(
                name="test",
                backend="mock",
                role_kind="reader",
                triggers=["review.approved"],
                publishes=["test.passed", "test.failed"],
            ),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
        ),
    )
    TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"source_commit": task_head, "source_branch": "worker/dev"},
    ))
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-1", title="x", status="backlog"))
    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    task = store.get("TASK-1")
    assert task is not None

    orch._dispatch_task(task, config.roles[1])  # type: ignore[attr-defined]

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert f"**HEAD**: `{task_head}`" in briefing
    assert f"**Base**: `{base}`" in briefing
    assert "worker-file.txt" in briefing
    assert "main-file.txt" not in briefing
    events = EventLog(state_dir / "events.jsonl").read_all()
    dispatched = next(e for e in events if e.type == "task.dispatched")
    assert dispatched.payload["target_ref"] == "task/TASK-1"
    assert dispatched.payload["base_git_head"] == base


def test_dispatch_preserves_existing_stage_status(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(
                name="test",
                backend="mock",
                role_kind="reader",
                triggers=["review.approved"],
                publishes=["test.passed", "test.failed"],
            ),
        ],
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-1",
        title="x",
        status="testing",
        assigned_to="test",
    ))
    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    task = store.get("TASK-1")
    assert task is not None

    orch._dispatch_task(task, config.roles[0])  # type: ignore[attr-defined]

    updated = store.get("TASK-1")
    assert updated is not None
    assert updated.status == "testing"
    assert updated.assigned_to == "test"
    assert updated.active_dispatch_id
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not [e for e in events if e.type == "task.invalid_transition"]
    assert [e for e in events if e.type == "task.dispatched"]


def test_writer_retry_dispatch_includes_latest_rework_context(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(
                name="dev",
                backend="mock",
                role_kind="writer",
                publishes=["dev.build.done"],
            ),
        ],
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-1",
        title="x",
        status="in_progress",
        assigned_to="dev",
        retry_count=1,
    ))
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="review.rejected",
        actor="review",
        task_id="TASK-1",
        payload={
            "summary": "schema-invalid final line must fail closed",
            "required_action": "rethrow SessionJsonlReplayError for schema-invalid tail",
            "checks": [
                {
                    "command": "node schema-invalid-tail probe",
                    "passed": False,
                    "summary": "returned degraded",
                }
            ],
        },
    ))
    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    task = store.get("TASK-1")
    assert task is not None

    orch._dispatch_task(task, config.roles[0])  # type: ignore[attr-defined]

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "## Rework Context" in briefing
    assert "review.rejected" in briefing
    assert "schema-invalid final line must fail closed" in briefing
    assert "rethrow SessionJsonlReplayError" in briefing


def test_reader_retry_dispatch_includes_latest_rework_context(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(
                name="arch",
                backend="mock",
                role_kind="reader",
                publishes=["arch.proposal.done"],
            ),
        ],
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-1",
        title="x",
        status="in_progress",
        assigned_to="arch",
        retry_count=1,
    ))
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="gate.failed",
        actor="critic",
        task_id="TASK-1",
        payload={
            "summary": "VS1 backlog contract cannot be dispatched",
            "details": "x" * 2600,
            "must_fix": [
                "expand each backlog task with spec_ref and tdd_ref",
                "confirm package namespace before scaffold",
            ],
        },
    ))
    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    task = store.get("TASK-1")
    assert task is not None

    orch._dispatch_task(task, config.roles[0])  # type: ignore[attr-defined]

    briefing = transport.sent[0][1].read_text(encoding="utf-8")
    assert "## Rework Context" in briefing
    assert "gate.failed" in briefing
    assert "VS1 backlog contract cannot be dispatched" in briefing
    assert "### Required Rework Items" in briefing
    assert "expand each backlog task with spec_ref and tdd_ref" in briefing
    assert "confirm package namespace before scaffold" in briefing


def test_writer_retry_dispatch_syncs_to_existing_task_ref(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", role_kind="writer"),
        ],
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
    task_head = _git(project_path, "rev-parse", "HEAD")
    _git(tmp_path, "update-ref", "refs/heads/task/TASK-1", task_head)
    refs_dir = state_dir / "refs"
    refs_dir.mkdir()
    (refs_dir / "task-index.json").write_text(
        json.dumps({
            "TASK-1": {
                "task_ref": "task/TASK-1",
                "source_commit": task_head,
                "source_branch": "worker/dev",
            }
        }) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("main moved\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "advance main")
    main_head = _git(tmp_path, "rev-parse", "HEAD")
    _git(project_path, "reset", "--hard", main_head)

    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-1",
        title="x",
        status="in_progress",
        assigned_to="dev",
        retry_count=1,
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="review.rejected",
        actor="review",
        task_id="TASK-1",
        payload={"summary": "fix task"},
    ))
    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    task = store.get("TASK-1")
    assert task is not None

    assert orch._dispatch_task(task, config.roles[0]) is True  # type: ignore[attr-defined]

    assert _git(project_path, "rev-parse", "HEAD") == task_head
    dispatched = next(
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "task.dispatched"
    )
    assert dispatched.payload["base_git_head"] == task_head


def test_writer_dispatch_syncs_to_feature_candidate_when_available(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", role_kind="writer"),
        ],
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
    base_branch = _git(tmp_path, "rev-parse", "--abbrev-ref", "HEAD")

    _git(tmp_path, "checkout", "-q", "-B", "candidate/F-11111111", base_branch)
    (tmp_path / "p0.txt").write_text("p0 scaffold\n", encoding="utf-8")
    _git(tmp_path, "add", "p0.txt")
    _git(tmp_path, "commit", "-q", "-m", "p0 scaffold")
    candidate_head = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", base_branch)
    _git(project_path, "reset", "--hard", base_branch)

    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-2",
        title="follow-up",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(feature_id="F-11111111"),
    ))
    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    task = store.get("TASK-2")
    assert task is not None

    assert orch._dispatch_task(task, config.roles[0]) is True  # type: ignore[attr-defined]

    assert _git(project_path, "rev-parse", "HEAD") == candidate_head
    assert (project_path / "p0.txt").read_text(encoding="utf-8") == "p0 scaffold\n"
    synced = next(
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "workdir.writer_synced"
    )
    assert synced.payload["source_ref"] == candidate_head


def test_task_ref_manager_scopes_diff_window_to_payload_base_git_head(
    tmp_path: Path,
) -> None:
    """Lane branches keep prior-round commits that never merge back to the
    candidate base (HIC-6B747D9856). With base_git_head pinned at dispatch,
    the scope gate must judge only the task's own increment, not the whole
    lane history since merge-base with the orchestrator HEAD."""
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="scoped task",
        status="in_progress",
        contract=TaskContract(scope=["src/task.py"]),
    ))
    base_branch = _git(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-q", "worker/dev")
    (tmp_path / "legacy.md").write_text("prior round\n", encoding="utf-8")
    _git(tmp_path, "add", "legacy.md")
    _git(tmp_path, "commit", "-q", "-m", "prior round, shipped elsewhere")
    lane_base = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "task.py").write_text("print('ok')\n", encoding="utf-8")
    _git(tmp_path, "add", "src/task.py")
    _git(tmp_path, "commit", "-q", "-m", "task increment")
    source_commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", base_branch)

    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "source_commit": source_commit,
            "source_branch": "worker/dev",
            "base_git_head": lane_base,
        },
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "rev-parse", "refs/heads/task/TASK-1") == source_commit


def test_task_ref_manager_accepts_host_repo_prefixed_paths_within_scope(
    tmp_path: Path,
) -> None:
    """Refactor task_maps author allowed_paths in the target-project frame
    (packages/pi-core/**) while git reports host-repo paths
    (cj-min/packages/pi-core/...). One leading component of frame shift
    must not reject an otherwise in-scope handoff (HIC-6B747D9856)."""
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="scoped task",
        status="in_progress",
        contract=TaskContract(scope=["packages/pi-core/**", "package.json"]),
    ))
    base_branch = _git(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-q", "worker/dev")
    pkg = tmp_path / "cj-min" / "packages" / "pi-core" / "src"
    pkg.mkdir(parents=True)
    (pkg / "index.ts").write_text("export {};\n", encoding="utf-8")
    (tmp_path / "cj-min" / "package.json").write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", "cj-min")
    _git(tmp_path, "commit", "-q", "-m", "pi-core slice under host subdir")
    source_commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", base_branch)

    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"source_commit": source_commit, "source_branch": "worker/dev"},
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "rev-parse", "refs/heads/task/TASK-1") == source_commit


def test_task_ref_manager_rejects_sibling_slice_paths_despite_prefix_tolerance(
    tmp_path: Path,
) -> None:
    """The one-component frame tolerance must not relax slice isolation:
    a sibling slice's files stay out of scope even under the host prefix."""
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="scoped task",
        status="in_progress",
        contract=TaskContract(scope=["packages/pi-core/**"]),
    ))
    base_branch = _git(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-q", "worker/dev")
    own = tmp_path / "cj-min" / "packages" / "pi-core"
    own.mkdir(parents=True)
    (own / "index.ts").write_text("export {};\n", encoding="utf-8")
    sibling = tmp_path / "cj-min" / "packages" / "gateway"
    sibling.mkdir(parents=True)
    (sibling / "routes.ts").write_text("export {};\n", encoding="utf-8")
    _git(tmp_path, "add", "cj-min")
    _git(tmp_path, "commit", "-q", "-m", "pi-core slice overlaps gateway")
    source_commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", base_branch)

    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"source_commit": source_commit, "source_branch": "worker/dev"},
    )

    result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).process_dev_build_done(event)

    assert result is not None
    assert result.status == "rejected"
    assert result.payload["reason"] == "source_commit changes outside task contract scope"
    assert result.payload["out_of_scope_files"] == ["cj-min/packages/gateway/routes.ts"]
