"""B-1203-06 R-3 — CodexSessionReader.session_path falls back to cwd
lookup when session_id is empty or uuid glob fails.

Real-world scenario (run 6 mixed-e2e smoke):
  1. Harness boots, codex role spawns
  2. ``observe_codex_session`` hasn't captured uuid yet
  3. ``_check_context_thresholds`` runs with session_id=""
  4. CodexSessionReader.session_path returns None → no usage synthesized
  5. Cost tracker ends up with 0 codex entries even though codex is running

Fix: if the uuid-based lookup misses, glob by cwd (read each candidate's
first-line session_meta and match payload.cwd against project_root).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.runtime.backend_session_reader import CodexSessionReader


def _write_rollout(path: Path, uuid_str: str, cwd: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "timestamp": "2026-04-21T00:00:00.000Z",
        "type": "session_meta",
        "payload": {
            "id": uuid_str, "cwd": cwd, "originator": "codex-tui",
        },
    }
    path.write_text(json.dumps(meta) + "\n")


def test_session_path_falls_back_to_cwd_when_uuid_empty(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(
        "zf.runtime.backend_session_reader.CODEX_SESSIONS_ROOT",
        tmp_path / "sessions",
    )
    project = "/tmp/my-project"

    uuid_str = "019dab2d-37cc-7440-aac2-a1826c7fdfc2"
    rollout = (
        tmp_path / "sessions/2026/04/21" /
        f"rollout-2026-04-21T01-00-00-{uuid_str}.jsonl"
    )
    _write_rollout(rollout, uuid_str, project)

    reader = CodexSessionReader()
    # Empty session_id → uuid glob finds nothing → cwd fallback wins
    result = reader.session_path(project, "", cached_path=None)
    assert result is not None
    assert str(result) == str(rollout)


def test_session_path_falls_back_to_cwd_when_uuid_mismatches(
    tmp_path: Path, monkeypatch
):
    """uuid pattern doesn't match any file, but a cwd-matching rollout
    exists — use it."""
    monkeypatch.setattr(
        "zf.runtime.backend_session_reader.CODEX_SESSIONS_ROOT",
        tmp_path / "sessions",
    )
    project = "/tmp/my-project"
    ours_uuid = "019dab2d-37cc-7440-aac2-a1826c7fdfc2"
    ours = (
        tmp_path / "sessions/2026/04/21" /
        f"rollout-2026-04-21T01-00-00-{ours_uuid}.jsonl"
    )
    _write_rollout(ours, ours_uuid, project)

    reader = CodexSessionReader()
    # uuid doesn't match any file → glob fails → cwd fallback finds ours
    result = reader.session_path(
        project, "unrelated-uuid-0000-0000-0000-000000000000",
    )
    assert result is not None
    assert str(result) == str(ours)


def test_cwd_fallback_skips_mismatched_projects(
    tmp_path: Path, monkeypatch
):
    """Fallback must not leak other projects' rollouts into ours."""
    monkeypatch.setattr(
        "zf.runtime.backend_session_reader.CODEX_SESSIONS_ROOT",
        tmp_path / "sessions",
    )
    ours = "/tmp/my-project"
    theirs = "/tmp/other-project"

    theirs_uuid = "019dab2d-9999-7440-aac2-ffffffffffff"
    theirs_file = (
        tmp_path / "sessions/2026/04/21" /
        f"rollout-2026-04-21T01-00-01-{theirs_uuid}.jsonl"
    )
    _write_rollout(theirs_file, theirs_uuid, theirs)

    reader = CodexSessionReader()
    result = reader.session_path(ours, "", cached_path=None)
    # No rollout matches our cwd → None
    assert result is None


def test_cwd_fallback_picks_newest_match(tmp_path: Path, monkeypatch):
    """When multiple rollouts match our cwd, return the newest."""
    import os as _os
    import time as _time

    monkeypatch.setattr(
        "zf.runtime.backend_session_reader.CODEX_SESSIONS_ROOT",
        tmp_path / "sessions",
    )
    project = "/tmp/my-project"

    old_uuid = "019d1111-1111-7440-aa11-111111111111"
    new_uuid = "019d2222-2222-7440-aa22-222222222222"
    old = (
        tmp_path / "sessions/2026/04/20" /
        f"rollout-2026-04-20T10-00-00-{old_uuid}.jsonl"
    )
    new = (
        tmp_path / "sessions/2026/04/21" /
        f"rollout-2026-04-21T10-00-00-{new_uuid}.jsonl"
    )
    _write_rollout(old, old_uuid, project)
    _write_rollout(new, new_uuid, project)
    # Explicitly set mtimes to ensure ordering
    now = _time.time()
    _os.utime(old, (now - 3600, now - 3600))
    _os.utime(new, (now, now))

    reader = CodexSessionReader()
    result = reader.session_path(project, "", cached_path=None)
    assert result is not None
    assert str(result) == str(new)


def test_cwd_fallback_preserves_cached_path_short_circuit(
    tmp_path: Path, monkeypatch
):
    """Back-compat: when a valid cached_path is provided, use it and
    never enter the fallback logic."""
    monkeypatch.setattr(
        "zf.runtime.backend_session_reader.CODEX_SESSIONS_ROOT",
        tmp_path / "sessions",
    )
    project = "/tmp/my-project"

    # Arbitrary file — cached_path short-circuits before cwd check
    cached = tmp_path / "cached-rollout.jsonl"
    cached.write_text("doesn't matter")
    reader = CodexSessionReader()
    result = reader.session_path(project, "", cached_path=cached)
    assert result == cached
