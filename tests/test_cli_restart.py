"""Tests for zf restart command."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from zf.cli.main import main
from zf.core.events import EventLog, ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry


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

    def test_full_restart_dry_run_does_not_stop_or_start(
        self, project_dir: Path, monkeypatch, capsys,
    ) -> None:
        def forbidden(_args):
            pytest.fail("dry-run must not stop or start a harness")

        monkeypatch.setattr("zf.cli.stop.run", forbidden)
        monkeypatch.setattr("zf.cli.start.run", forbidden)
        assert main(["restart", "--dry-run"]) == 0
        assert "would stop and restart" in capsys.readouterr().out

    def test_restart_role_dry_run(self, project_dir: Path, capsys):
        # Start first so there's a session context
        main(["start", "--dry-run"])
        result = main(["restart", "dev", "--dry-run"])
        assert result == 0
        captured = capsys.readouterr()
        assert "dev" in captured.out.lower()

    def test_restart_propagates_exact_cli_command(
        self,
        project_dir: Path,
        monkeypatch,
    ):
        monkeypatch.delenv("ZF_CLI_CMD", raising=False)

        result = main(["restart", "dev", "--dry-run"])

        assert result == 0
        assert os.environ["ZF_CLI_CMD"] != "zf"
        assert "zf" in os.environ["ZF_CLI_CMD"]

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

    def test_pending_recycle_codex_restart_uses_fresh_session(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        config = {
            "version": "1.0",
            "project": {"name": "test", "state_dir": ".zf"},
            "session": {"tmux_session": "test-zf-recycle"},
            "roles": [{"name": "judge-issue", "backend": "codex"}],
        }
        (tmp_path / "zf.yaml").write_text(yaml.dump(config))
        assert main(["init"]) == 0
        assert main(["start", "--dry-run"]) == 0
        state_dir = tmp_path / ".zf"
        old_rollout = tmp_path / "old-rollout.jsonl"
        old_rollout.write_text("old provider transcript\n", encoding="utf-8")
        old_session = "22222222-2222-2222-2222-222222222222"
        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(tmp_path),
        )
        assert registry.bind_codex_session(
            "judge-issue",
            old_session,
            session_path=old_rollout,
            observed_from="test",
        )
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="worker.state.changed",
            actor="judge-issue",
            payload={
                "instance_id": "judge-issue",
                "from": "busy",
                "to": "pending_recycle",
                "reason": "recycle_threshold_exceeded",
            },
        ))

        assert main(["restart", "judge-issue", "--dry-run"]) == 0

        refreshed = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(tmp_path),
        )
        assert refreshed.get("judge-issue") is None
        assert refreshed.get_path("judge-issue") is None
        assert old_rollout.exists()
        launch = json.loads(
            (state_dir / "workdirs" / "judge-issue" / "runtime" / "launch.json")
            .read_text(encoding="utf-8")
        )
        argv = [str(item) for item in launch["argv"]]
        assert old_session not in argv
        assert "resume" not in argv
        events = EventLog(state_dir / "events.jsonl").read_all()
        recycling = [
            event
            for event in events
            if event.type == "worker.recycling"
            and event.actor == "judge-issue"
        ]
        assert recycling[-1].payload["session_strategy"] == (
            "fresh_context_recycle_clear_codex"
        )
        assert recycling[-1].payload["reason"] == "manual_context_recycle"
        states = [
            str(event.payload.get("to") or "")
            for event in events
            if event.type == "worker.state.changed"
            and event.actor == "judge-issue"
        ]
        assert states[-1] == "idle"

    def test_pending_recycle_claude_restart_rotates_session(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        config = {
            "version": "1.0",
            "project": {"name": "test", "state_dir": ".zf"},
            "session": {"tmux_session": "test-zf-recycle-claude"},
            "roles": [{"name": "judge-issue", "backend": "claude-code"}],
        }
        (tmp_path / "zf.yaml").write_text(yaml.dump(config))
        assert main(["init"]) == 0
        assert main(["start", "--dry-run"]) == 0
        state_dir = tmp_path / ".zf"
        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(tmp_path),
        )
        old_session = registry.get("judge-issue")
        assert old_session is not None
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="worker.state.changed",
            actor="judge-issue",
            payload={
                "instance_id": "judge-issue",
                "from": "busy",
                "to": "pending_recycle",
                "reason": "recycle_threshold_exceeded",
            },
        ))

        assert main(["restart", "judge-issue", "--dry-run"]) == 0

        refreshed = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(tmp_path),
        )
        new_session = refreshed.get("judge-issue")
        assert new_session is not None
        assert new_session != old_session
        launch = json.loads(
            (state_dir / "workdirs" / "judge-issue" / "runtime" / "launch.json")
            .read_text(encoding="utf-8")
        )
        argv = [str(item) for item in launch["argv"]]
        assert str(old_session) not in argv
        assert str(new_session) in argv
        assert "--resume" not in argv
        events = EventLog(state_dir / "events.jsonl").read_all()
        recycling = [
            event
            for event in events
            if event.type == "worker.recycling"
            and event.actor == "judge-issue"
        ]
        assert recycling[-1].payload["session_strategy"] == (
            "fresh_context_recycle_rotated_session"
        )
        assert recycling[-1].payload["reason"] == "manual_context_recycle"
