"""1231-T4: unit cover _dismiss_codex_update_prompt.

Called on every codex spawn in SpawnCoordinator but never previously
tested. 4 scenarios:
  1. version.json missing        → no-op, no crash
  2. malformed JSON              → no-op, no crash
  3. latest_version unset        → no-op, no write
  4. dismissed_version != latest → rewrite with dismissed = latest
  5. already dismissed           → no rewrite (idempotent)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from zf.runtime import spawn_coordinator as sc_mod


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # `~` resolves via os.path.expanduser which reads $HOME on POSIX.
    # That's already patched above; no further work needed.
    return tmp_path


def test_missing_version_file_is_noop(fake_home: Path):
    # Absent file → function should return without raising
    assert not (fake_home / ".codex" / "version.json").exists()
    sc_mod._dismiss_codex_update_prompt()
    assert not (fake_home / ".codex" / "version.json").exists()


def test_malformed_json_is_noop(fake_home: Path):
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    version_file = codex_dir / "version.json"
    version_file.write_text("{not valid json")
    before = version_file.read_text()
    sc_mod._dismiss_codex_update_prompt()
    # File should not be touched
    assert version_file.read_text() == before


def test_latest_version_unset_is_noop(fake_home: Path):
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    version_file = codex_dir / "version.json"
    # No latest_version key → nothing to dismiss
    version_file.write_text(json.dumps({"last_checked_at": "..."}))
    before = version_file.read_text()
    sc_mod._dismiss_codex_update_prompt()
    assert version_file.read_text() == before


def test_new_latest_triggers_dismiss(fake_home: Path):
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    version_file = codex_dir / "version.json"
    version_file.write_text(json.dumps({
        "latest_version": "2.5.0",
        "last_checked_at": "now",
        "dismissed_version": None,
    }))
    sc_mod._dismiss_codex_update_prompt()
    data = json.loads(version_file.read_text())
    assert data["dismissed_version"] == "2.5.0"
    # Other fields preserved
    assert data["last_checked_at"] == "now"


def test_already_dismissed_is_idempotent(fake_home: Path):
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    version_file = codex_dir / "version.json"
    payload = {
        "latest_version": "2.5.0",
        "dismissed_version": "2.5.0",
        "last_checked_at": "now",
    }
    version_file.write_text(json.dumps(payload))
    before_mtime = version_file.stat().st_mtime_ns
    sc_mod._dismiss_codex_update_prompt()
    after_mtime = version_file.stat().st_mtime_ns
    assert before_mtime == after_mtime, \
        "file should not be rewritten when already dismissed"
