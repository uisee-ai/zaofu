"""Tests for zf restart command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from zf.cli.main import main


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "test", "state_dir": ".zf"},
        "session": {"tmux_session": "test-zf"},
        "roles": [
            {"name": "dev", "backend": "mock"},
            {"name": "review", "backend": "mock"},
        ],
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


class TestRestart:
    def test_restart_registered(self):
        with pytest.raises(SystemExit) as exc:
            main(["restart", "--help"])
        assert exc.value.code == 0

    def test_restart_role_dry_run(self, project_dir: Path, capsys):
        # Start first so there's a session context
        main(["start", "--dry-run"])
        result = main(["restart", "dev", "--dry-run"])
        assert result == 0
        captured = capsys.readouterr()
        assert "dev" in captured.out.lower()

    def test_restart_unknown_role(self, project_dir: Path):
        result = main(["restart", "nonexistent", "--dry-run"])
        assert result != 0

    def test_restart_emits_event(self, project_dir: Path):
        main(["start", "--dry-run"])
        main(["restart", "dev", "--dry-run"])
        events = (project_dir / ".zf" / "events.jsonl").read_text()
        assert "worker.restarted" in events

    def test_restart_uses_project_state_dir(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = {
            "version": "1.0",
            "project": {"name": "test", "state_dir": "runtime-state"},
            "session": {"tmux_session": "test-zf"},
            "roles": [{"name": "dev", "backend": "mock"}],
        }
        (tmp_path / "zf.yaml").write_text(yaml.dump(config))
        main(["init"])
        main(["start", "--dry-run"])

        result = main(["restart", "dev", "--dry-run"])

        assert result == 0
        state_dir = tmp_path / "runtime-state"
        assert (state_dir / "instructions" / "dev.md").exists()
        assert "worker.restarted" in (state_dir / "events.jsonl").read_text()
        assert not (tmp_path / ".zf").exists()

    def test_restart_without_zf_yaml(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = main(["restart", "--dry-run"])
        assert result != 0
