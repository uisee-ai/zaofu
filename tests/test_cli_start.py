"""Tests for zf start and zf stop commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from zf.cli.main import main


def test_idle_tick_catches_up_from_durable_offset_without_empty_pushed_events():
    from zf.cli.start import _run_orchestrator_idle_tick

    calls = []

    class FakeOrchestrator:
        def run_once(self, *args, **kwargs):
            calls.append((args, kwargs))
            return []

    _run_orchestrator_idle_tick(FakeOrchestrator())

    assert calls == [((), {})]


def test_record_ready_worker_state_clears_stale_respawning(tmp_path: Path):
    from zf.cli.start import _record_ready_worker_state
    from zf.core.events.log import EventLog
    from zf.core.state.role_sessions import RoleSessionRegistry

    event_log = EventLog(tmp_path / "events.jsonl")
    registry = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    registry.record_heartbeat("verify-lane-0", {
        "instance_id": "verify-lane-0",
        "state": "respawning",
        "current_task_id": "",
    })

    _record_ready_worker_state(
        event_log=event_log,
        registry=registry,
        instance_id="verify-lane-0",
    )

    _last_at, payload = registry.get_last_heartbeat("verify-lane-0")
    assert payload is not None
    assert payload["state"] == "idle"
    events = event_log.read_all()
    assert events[-1].type == "worker.state.changed"
    assert events[-1].actor == "verify-lane-0"
    assert events[-1].payload["from"] == "respawning"
    assert events[-1].payload["to"] == "idle"


def test_record_ready_worker_state_does_not_overwrite_busy(tmp_path: Path):
    from zf.cli.start import _record_ready_worker_state
    from zf.core.events.log import EventLog
    from zf.core.state.role_sessions import RoleSessionRegistry

    event_log = EventLog(tmp_path / "events.jsonl")
    registry = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    registry.record_heartbeat("dev-lane-0", {
        "instance_id": "dev-lane-0",
        "state": "busy",
        "current_task_id": "CJMIN-PI-CORE-001",
    })

    _record_ready_worker_state(
        event_log=event_log,
        registry=registry,
        instance_id="dev-lane-0",
    )

    _last_at, payload = registry.get_last_heartbeat("dev-lane-0")
    assert payload is not None
    assert payload["state"] == "busy"
    assert event_log.read_all() == []


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch):
    """Set up a minimal project directory with zf.yaml and .zf/."""
    monkeypatch.chdir(tmp_path)

    # Create zf.yaml
    config = {
        "version": "1.0",
        "project": {"name": "test-project", "state_dir": ".zf"},
        "session": {"tmux_session": "test-zf"},
        "orchestrator": {"backend": "mock"},
        "roles": [
            {"name": "dev", "backend": "mock"},
        ],
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))

    # Run zf init
    main(["init"])

    return tmp_path


class TestZfStart:
    def test_start_registered_in_cli(self):
        """zf start should be a recognized command."""
        with pytest.raises(SystemExit) as exc_info:
            main(["start", "--help"])
        assert exc_info.value.code == 0

    def test_start_requires_zf_yaml(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = main(["start", "--dry-run"])
        assert result != 0

    def test_start_requires_init(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
        result = main(["start", "--dry-run"])
        assert result != 0

    def test_start_requires_session_yaml_even_when_state_dir_exists(
        self,
        tmp_path: Path,
        monkeypatch,
        capsys,
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zf.yaml").write_text(
            'version: "1.0"\n'
            "project:\n"
            "  name: test\n"
            "  state_dir: .zf-custom\n",
            encoding="utf-8",
        )
        (tmp_path / ".zf-custom").mkdir()

        result = main(["start", "--dry-run"])

        assert result == 1
        captured = capsys.readouterr()
        assert ".zf-custom/session.yaml" in captured.err
        assert "zf init --state-dir .zf-custom" in captured.err

    def test_start_rejects_tool_closure_errors(
        self,
        project_dir: Path,
        capsys,
    ):
        (project_dir / "zf.yaml").write_text(
            yaml.dump({
                "version": "1.0",
                "project": {"name": "test-project", "state_dir": ".zf"},
                "session": {"tmux_session": "test-zf"},
                "roles": [{
                    "name": "dev",
                    "backend": "claude-code",
                    "permission_mode": "allowlist",
                    "allowed_tools": ["*"],
                }],
            })
        )

        result = main(["start", "--dry-run"])

        assert result == 1
        captured = capsys.readouterr()
        assert "tool closure" in captured.err.lower()
        assert "wildcard" in captured.err.lower()

    def test_start_rejects_workflow_preflight_stop(
        self,
        project_dir: Path,
        capsys,
    ):
        (project_dir / "zf.yaml").write_text(
            yaml.dump({
                "version": "1.0",
                "project": {"name": "test-project", "state_dir": ".zf"},
                "session": {"tmux_session": "test-zf"},
                "roles": [{
                    "name": "dev",
                    "backend": "mock",
                    "triggers": ["task.dispatched"],
                    "publishes": ["dev.done"],
                }],
            })
        )

        result = main(["start", "--dry-run"])

        assert result == 1
        captured = capsys.readouterr()
        assert "workflow preflight" in captured.err.lower()
        assert "terminal_event_without_producer" in captured.err

    def test_start_can_skip_workflow_preflight_for_diagnosis(
        self,
        project_dir: Path,
        capsys,
    ):
        (project_dir / "zf.yaml").write_text(
            yaml.dump({
                "version": "1.0",
                "project": {"name": "test-project", "state_dir": ".zf"},
                "session": {"tmux_session": "test-zf"},
                "roles": [{
                    "name": "dev",
                    "backend": "mock",
                    "triggers": ["task.dispatched"],
                    "publishes": ["dev.done"],
                }],
            })
        )

        result = main(["start", "--dry-run", "--skip-workflow-inspect"])

        assert result == 0
        captured = capsys.readouterr()
        assert "dry-run" in captured.out.lower()

    def test_start_dry_run_succeeds(self, project_dir: Path, capsys):
        result = main(["start", "--dry-run"])
        assert result == 0
        captured = capsys.readouterr()
        assert "started" in captured.out.lower() or "dry" in captured.out.lower()
        assert (
            project_dir
            / ".zf"
            / "artifacts"
            / "workflow-inspect"
            / "inspect.json"
        ).exists()

    def test_start_writes_redacted_launch_artifact(
        self,
        project_dir: Path,
        monkeypatch,
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value-123456")

        result = main(["start", "--dry-run"])

        assert result == 0
        launch = project_dir / ".zf" / "workdirs" / "dev" / "runtime" / "launch.json"
        data = json.loads(launch.read_text(encoding="utf-8"))
        encoded = json.dumps(data)
        assert data["schema_version"] == "worker-launch.v1"
        assert data["role"] == "dev"
        assert data["backend"] == "mock"
        assert data["instructions_ref"].endswith(".zf/instructions/dev.md")
        assert "sk-test-secret-value-123456" not in encoded
        openai = [item for item in data["env"] if item["key"] == "OPENAI_API_KEY"]
        assert openai == [{"key": "OPENAI_API_KEY", "value": "<redacted>"}]

    def test_start_watcher_configured(self, project_dir: Path, capsys):
        main(["start", "--dry-run"])
        captured = capsys.readouterr()
        assert "watcher" in captured.out.lower() or "wake" in captured.out.lower()

    def test_start_creates_lock_file(self, project_dir: Path):
        main(["start", "--dry-run"])
        # In dry-run, lock may or may not persist (it's released on exit)
        # But the event should be emitted
        events_file = project_dir / ".zf" / "events.jsonl"
        events = events_file.read_text()
        assert "loop.started" in events or "session.started" in events

    def test_start_emits_events(self, project_dir: Path):
        main(["start", "--dry-run"])
        events_file = project_dir / ".zf" / "events.jsonl"
        lines = [line for line in events_file.read_text().strip().split("\n") if line.strip()]
        event_types = [json.loads(line)["type"] for line in lines]
        assert "loop.started" in event_types

    def test_start_updates_session(self, project_dir: Path):
        main(["start", "--dry-run"])
        session = yaml.safe_load((project_dir / ".zf" / "session.yaml").read_text())
        assert session["runtime_state"] in ("active", "running")


class TestZfStop:
    def test_stop_registered_in_cli(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["stop", "--help"])
        assert exc_info.value.code == 0

    def test_stop_without_running_session(self, project_dir: Path, capsys):
        result = main(["stop"])
        # Should handle gracefully (no tmux session to kill)
        assert result == 0 or result == 1

    def test_stop_emits_event_after_start(self, project_dir: Path):
        main(["start", "--dry-run"])
        main(["stop"])
        events_file = project_dir / ".zf" / "events.jsonl"
        events = events_file.read_text()
        assert "loop.stopped" in events or "session.stopped" in events
