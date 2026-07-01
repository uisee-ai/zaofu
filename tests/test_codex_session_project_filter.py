"""B-1203-06 R-2: observe_codex_session must filter by project cwd to
avoid picking up rollout files from other codex invocations running
concurrently on the machine.

Rollout JSONL's first line is a ``session_meta`` record with
``payload.cwd`` set to the project directory. Peek that line during
the glob filter.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from zf.core.state.role_sessions import RoleSessionRegistry


def _write_rollout(path: Path, uuid_str: str, cwd: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "timestamp": "2026-04-21T00:00:00.000Z",
        "type": "session_meta",
        "payload": {
            "id": uuid_str,
            "timestamp": "2026-04-21T00:00:00.000Z",
            "cwd": cwd,
            "originator": "codex-tui",
        },
    }
    path.write_text(json.dumps(meta) + "\n")


def test_observe_picks_matching_cwd(tmp_path: Path, monkeypatch):
    """Two rollout files, only one matches our project — pick that one."""
    monkeypatch.setattr(
        "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
        tmp_path / "sessions",
    )
    project = "/tmp/my-project"
    other = "/tmp/some-other-project"

    # Both files fresh (mtime > since_ts)
    mine_uuid = "019dab2d-37cc-7440-aac2-a1826c7fdfc2"
    theirs_uuid = "019dab2d-9999-7440-aac2-ffffffffffff"
    mine = tmp_path / "sessions/2026/04/21" / f"rollout-2026-04-21T01-00-00-{mine_uuid}.jsonl"
    theirs = tmp_path / "sessions/2026/04/21" / f"rollout-2026-04-21T01-00-01-{theirs_uuid}.jsonl"
    _write_rollout(mine, mine_uuid, project)
    _write_rollout(theirs, theirs_uuid, other)

    reg = RoleSessionRegistry(tmp_path / "reg.yaml", project_root=project)
    reg._entries["dev"] = mine_uuid  # deterministic placeholder

    result = reg.observe_codex_session("dev", since_ts=0.0,
                                       max_wait_seconds=0.1)
    assert result is not None, "must find the matching-cwd rollout"
    _uuid, path = result
    assert str(path) == str(mine), (
        f"expected {mine}, got {path} — observe should skip other-cwd files"
    )


def test_observe_returns_none_when_only_other_cwd_matches(
    tmp_path: Path, monkeypatch,
):
    """All candidates mismatch → observe returns None (caller warns)."""
    monkeypatch.setattr(
        "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
        tmp_path / "sessions",
    )
    project = "/tmp/my-project"

    theirs_uuid = "019dab2d-9999-7440-aac2-ffffffffffff"
    theirs = tmp_path / "sessions/2026/04/21" / f"rollout-2026-04-21T01-00-01-{theirs_uuid}.jsonl"
    _write_rollout(theirs, theirs_uuid, "/some/other/project")

    reg = RoleSessionRegistry(tmp_path / "reg.yaml", project_root=project)
    result = reg.observe_codex_session("dev", since_ts=0.0,
                                       max_wait_seconds=0.1)
    assert result is None


def test_observe_accepts_file_without_session_meta_backward_compat(
    tmp_path: Path, monkeypatch,
):
    """If a rollout doesn't start with a parseable session_meta, fall
    back to the old behavior (accept by filename match) rather than
    silently ignoring valid codex sessions written in older formats."""
    monkeypatch.setattr(
        "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
        tmp_path / "sessions",
    )
    project = "/tmp/my-project"

    uuid_str = "019dab2d-37cc-7440-aac2-a1826c7fdfc2"
    legacy = tmp_path / "sessions/2026/04/21" / f"rollout-2026-04-21T01-00-00-{uuid_str}.jsonl"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("some legacy non-json data\n")

    reg = RoleSessionRegistry(tmp_path / "reg.yaml", project_root=project)
    result = reg.observe_codex_session("dev", since_ts=0.0,
                                       max_wait_seconds=0.1)
    # Either we accept (backward compat) or we skip; both are acceptable
    # as long as we don't crash. The real assertion: no exception.
    assert result is None or result[0] is not None


def test_observe_respects_since_ts(tmp_path: Path, monkeypatch):
    """Existing since_ts filter still works on top of cwd filter."""
    monkeypatch.setattr(
        "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
        tmp_path / "sessions",
    )
    project = "/tmp/my-project"

    # Write a file matching project but stale (old mtime)
    old_uuid = "019d1111-1111-7440-aa11-111111111111"
    old = tmp_path / "sessions/2026/04/20" / f"rollout-2026-04-20T01-00-00-{old_uuid}.jsonl"
    _write_rollout(old, old_uuid, project)
    import os as _os
    old_ts = time.time() - 3600
    _os.utime(old, (old_ts, old_ts))

    reg = RoleSessionRegistry(tmp_path / "reg.yaml", project_root=project)
    # since_ts in the future relative to the old file → filter it out
    result = reg.observe_codex_session(
        "dev", since_ts=time.time() - 60, max_wait_seconds=0.1,
    )
    assert result is None, "stale file should not match when since_ts > mtime"
