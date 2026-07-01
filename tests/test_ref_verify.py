from __future__ import annotations

import json
import subprocess
from pathlib import Path

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.runtime.ref_verify import RefVerifier


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
    _git(root, "branch", "-M", "main")
    return _git(root, "rev-parse", "HEAD")


def _config(state_dir: Path) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
    )


def test_refs_verify_catches_missing_task_ref(tmp_path: Path):
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    refs_dir = state_dir / "refs"
    refs_dir.mkdir(parents=True)
    (refs_dir / "task-index.json").write_text(json.dumps({
        "TASK-1": {
            "task_id": "TASK-1",
            "task_ref": "task/TASK-1",
            "source_commit": head,
            "source_branch": "main",
        },
    }), encoding="utf-8")

    result = RefVerifier(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).verify()

    assert not result.ok
    assert "TASK-1: missing task ref task/TASK-1" in result.issues


def test_refs_verify_allows_moved_writer_source_branch(tmp_path: Path):
    head = _init_repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "worker/dev-1")
    (tmp_path / "task.txt").write_text("task\n", encoding="utf-8")
    _git(tmp_path, "add", "task.txt")
    _git(tmp_path, "commit", "-q", "-m", "task")
    task_commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "branch", "task/TASK-1", task_commit)
    _git(tmp_path, "reset", "--hard", head)

    state_dir = tmp_path / ".zf"
    refs_dir = state_dir / "refs"
    refs_dir.mkdir(parents=True)
    (refs_dir / "task-index.json").write_text(
        json.dumps(
            {
                "TASK-1": {
                    "task_id": "TASK-1",
                    "task_ref": "task/TASK-1",
                    "source_commit": task_commit,
                    "source_branch": "worker/dev-1",
                },
            }
        ),
        encoding="utf-8",
    )

    result = RefVerifier(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).verify()

    assert result.ok


def test_refs_verify_catches_missing_candidate_ref(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    manifest_dir = state_dir / "candidates" / "F-11111111"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({
        "pdd_id": "F-11111111",
        "branch": "candidate/F-11111111",
        "status": "updated",
    }), encoding="utf-8")

    result = RefVerifier(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir),
    ).verify()

    assert not result.ok
    assert "F-11111111: missing candidate ref candidate/F-11111111" in result.issues
