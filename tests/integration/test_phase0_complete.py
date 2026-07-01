"""Phase 0 completion integration test — full lifecycle with dry-run tmux."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.runtime.watcher import EventWatcher


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    """Create a complete project directory."""
    monkeypatch.chdir(tmp_path)

    config = {
        "version": "1.0",
        "preset": "safe-local",
        "project": {"name": "integration-test", "state_dir": ".zf"},
        "session": {"tmux_session": "test-zf"},
        "orchestrator": {"backend": "mock"},
        "roles": [
            {"name": "dev", "backend": "mock", "stages": ["implement"]},
            {"name": "review", "backend": "mock", "stages": ["code_review"]},
        ],
        "stage_labels": {"implement": "Build", "code_review": "Review"},
        "quality_gates": {
            "static": {"enabled": True, "required_checks": ["command_exit_zero"]},
        },
        # zf start's workflow preflight fail-closes when a gate failure
        # event has no rework route (STOP missing_rework_route).
        "workflow": {"rework_routing": {"static_gate.failed": "dev"}},
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    return tmp_path


class TestPhase0CompleteCycle:
    """Full lifecycle: init → start → agent activity → stop → verify."""

    def test_full_lifecycle(self, project: Path):
        # 1. Initialize
        assert main(["init"]) == 0
        state_dir = project / ".zf"
        assert state_dir.exists()
        assert (state_dir / "events.jsonl").exists()
        assert (state_dir / "kanban.json").exists()
        assert (state_dir / "session.yaml").exists()

        # 2. Start (dry-run)
        assert main(["start", "--dry-run"]) == 0

        # 3. Verify start emitted events
        event_log = EventLog(state_dir / "events.jsonl")
        events = event_log.read_all()
        event_types = [e.type for e in events]
        assert "session.started" in event_types
        assert "loop.started" in event_types

        # 4. Simulate agent activity: dev emits build done
        assert main(["emit", "dev.build.done", "--actor", "dev"]) == 0

        # 5. Verify watcher can detect the new event
        detected: list[str] = []
        watcher = EventWatcher(
            state_dir / "events.jsonl",
            on_event=lambda line: detected.append(line),
            wake_patterns=["dev.build.done"],
        )
        watcher.poll_once()
        # The watcher starts from end, so let's re-create to catch the emit
        watcher2 = EventWatcher(
            state_dir / "events.jsonl",
            on_event=lambda line: detected.append(line),
        )
        # Reset position to start to verify all events readable
        watcher2._file_pos = 0
        watcher2.poll_once()
        assert any("dev.build.done" in line for line in detected)

        # 6. Stop
        assert main(["stop"]) == 0

        # 7. Verify stop emitted events
        events = event_log.read_all()
        event_types = [e.type for e in events]
        assert "loop.stopped" in event_types

        # 8. Verify session state
        session_store = SessionStore(state_dir / "session.yaml")
        state = session_store.load()
        assert state.runtime_state == "stopped"

    def test_init_validates_after_start(self, project: Path):
        """Validate the config after init."""
        main(["init"])
        assert main(["validate"]) == 0

    def test_status_after_start(self, project: Path, capsys):
        """Status works after init + start."""
        main(["init"])
        main(["start", "--dry-run"])
        assert main(["status"]) == 0
        captured = capsys.readouterr()
        assert "integration-test" in captured.out or "active" in captured.out.lower()

    def test_events_query_after_activity(self, project: Path, capsys):
        """Events can be queried after activity."""
        main(["init"])
        main(["start", "--dry-run"])
        main(["emit", "test.event", "--actor", "test"])
        assert main(["events", "--last", "5"]) == 0
        captured = capsys.readouterr()
        assert "test.event" in captured.out

    def test_config_new_fields_parsed(self, project: Path):
        """Verify the config's new fields are accessible."""
        from zf.core.config.loader import load_config
        config = load_config(project / "zf.yaml")
        assert config.preset == "safe-local"
        assert "implement" in config.stage_labels
        assert "static" in config.quality_gates

    def test_double_start_prevented_by_lock(self, project: Path):
        """Second start should fail due to lock."""
        import fcntl
        main(["init"])
        # Hold the lock
        lock_path = project / ".zf" / "loop.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = lock_path.open("w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = main(["start", "--dry-run"])
            assert result != 0  # should fail
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
