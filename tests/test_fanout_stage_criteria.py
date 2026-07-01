from __future__ import annotations

import subprocess
from pathlib import Path

from zf.core.events.log import EventLog
from zf.runtime.fanout_stage_criteria import _sync_candidate_worktree_head


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return proc.stdout.strip()


def _make_candidate_repo(tmp_path: Path) -> tuple[Path, Path, EventLog, str, str]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _git(project_root, "init", "-q")
    _git(project_root, "config", "user.email", "test@example.com")
    _git(project_root, "config", "user.name", "Test User")
    (project_root / "README.md").write_text("v1\n", encoding="utf-8")
    _git(project_root, "add", "README.md")
    _git(project_root, "commit", "-q", "-m", "feat: v1")
    old_head = _git(project_root, "rev-parse", "HEAD")
    (project_root / "README.md").write_text("v2\n", encoding="utf-8")
    _git(project_root, "commit", "-q", "-am", "feat: v2")
    new_head = _git(project_root, "rev-parse", "HEAD")
    _git(project_root, "branch", "-f", "cand/R5", new_head)

    state_dir = tmp_path / ".zf"
    candidate_root = state_dir / "candidates" / "R5" / "worktree"
    candidate_root.parent.mkdir(parents=True)
    _git(project_root, "worktree", "add", "--detach", "-q", str(candidate_root), old_head)
    event_log = EventLog(state_dir / "events.jsonl")
    return project_root, candidate_root, event_log, old_head, new_head


def test_sync_candidate_worktree_head_fast_forwards_clean_stale_candidate(
    tmp_path: Path,
) -> None:
    project_root, candidate_root, event_log, old_head, new_head = _make_candidate_repo(
        tmp_path,
    )
    state_dir = tmp_path / ".zf"

    result = _sync_candidate_worktree_head(
        state_dir=state_dir,
        project_root=project_root,
        event_log=event_log,
        manifest={"pdd_id": "R5", "trace_id": "trace-r5", "target_ref": "cand/R5"},
        artifact_payload={"pdd_id": "R5", "target_ref": "cand/R5"},
        candidate_root=candidate_root,
    )

    assert result["status"] == "synced"
    assert result["worktree_synced"] is True
    assert result["worktree_head"] == old_head
    assert result["synced_to_head"] == new_head
    assert _git(candidate_root, "rev-parse", "HEAD") == new_head
    assert event_log.read_all() == []


def test_sync_candidate_worktree_head_refuses_dirty_stale_candidate(
    tmp_path: Path,
) -> None:
    project_root, candidate_root, event_log, _old_head, new_head = _make_candidate_repo(
        tmp_path,
    )
    state_dir = tmp_path / ".zf"
    (candidate_root / "scratch.txt").write_text("dirty\n", encoding="utf-8")

    result = _sync_candidate_worktree_head(
        state_dir=state_dir,
        project_root=project_root,
        event_log=event_log,
        manifest={"pdd_id": "R5", "trace_id": "trace-r5", "target_ref": "cand/R5"},
        artifact_payload={"pdd_id": "R5", "target_ref": "cand/R5"},
        candidate_root=candidate_root,
    )

    assert result["status"] == "dirty_stale"
    assert result["worktree_synced"] is False
    assert result["target_ref_head"] == new_head
    assert "?? scratch.txt" in result["dirty_paths"]
    assert _git(candidate_root, "rev-parse", "HEAD") != new_head
    events = event_log.read_all()
    assert events[-1].type == "candidate.worktree.stale"
    assert events[-1].correlation_id == "trace-r5"
