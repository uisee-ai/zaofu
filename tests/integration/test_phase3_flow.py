"""Phase 3 integration test — full features."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import yaml

from zf.cli.main import main
from zf.core.memory.store import MemoryStore, MemoryEntry
from zf.core.memory.staleness import StalenessChecker
from zf.core.verification.scope_ratchet import ScopeRatchet
from zf.core.config.tool_closure import validate_tool_closure
from zf.core.config.cold_start import cold_start_check
from zf.core.config.loader import load_config
from zf.runtime.escalation import EscalationManager
from zf.runtime.shutdown import GracefulShutdown
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "phase3-test", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    (tmp_path / "CLAUDE.md").write_text("# Test project")
    (tmp_path / "README.md").write_text("# Phase 3 Test")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    main(["init"])
    return tmp_path


class TestMemorySystem:
    def test_add_and_retrieve(self, project: Path):
        store = MemoryStore(project / ".zf" / "memory")
        store.add(None, "decision", "Use JWT for auth")
        store.add("dev", "pattern", "Always run tests")
        assert len(store.get(None)) == 1
        assert len(store.get("dev")) == 1

    def test_staleness_detection(self, project: Path):
        # G-MEM-4: age decay is now done at the storage layer via
        # MemoryStore.get(last_days=N). StalenessChecker only flags
        # entries whose referenced file paths no longer exist on disk.
        entry = MemoryEntry(
            type="pattern",
            content="See src/ghost.py for details",
            added_at=datetime.now(timezone.utc).isoformat(),
            max_days=60,
        )
        checker = StalenessChecker(project)
        stale = checker.check([entry])
        assert any(s.reason == "path_missing" for s in stale)

    def test_memory_cli(self, project: Path, capsys):
        main(["memory", "add", "shared", "Test memory", "--type", "decision"])
        main(["memory", "show"])
        captured = capsys.readouterr()
        assert "Test memory" in captured.out


# CheckpointManager removed 2026-04-20 (backlog P1-2): save() had
# production callers (shutdown, cleanup) but restore() was never called
# from production code — only from this test file. The shutdown
# snapshot was replaced by a simpler `.zf/last-shutdown/` dir; see
# TestLastShutdownSnapshot in this file and TestShutdownStep7 below.


class TestScopeRatchet:
    def test_detect_out_of_scope(self, project: Path):
        ratchet = ScopeRatchet(project)
        before = ratchet.snapshot()

        # Create a file in blocked area
        (project / ".zf" / "sneaky.txt").write_text("naughty")
        after = ratchet.snapshot()

        changed = ratchet.diff(before, after)
        violations = ratchet.check(changed, allowed=["src", "tests"], blocked=[".zf"])
        assert len(violations) >= 1
        assert any(v.reason == "in_blocked" for v in violations)


class TestToolClosure:
    def test_valid_config(self, project: Path):
        config = load_config(project / "zf.yaml")
        errors = validate_tool_closure(config)
        assert errors == []

    def test_wildcard_rejected(self, project: Path):
        from zf.core.config.schema import ZfConfig, ProjectConfig, RoleConfig
        config = ZfConfig(
            project=ProjectConfig(name="test"),
            roles=[RoleConfig(name="dev", allowed_tools=["*"])],
        )
        errors = validate_tool_closure(config)
        assert any("wildcard" in e for e in errors)


class TestEscalation:
    def test_escalate_and_resolve(self, project: Path):
        mgr = EscalationManager(project / ".zf")
        mgr.escalate("Need human decision", task_id="T1")

        # Simulate human writing steer
        mgr.steer_path.write_text("Approve the approach")
        assert mgr.has_steer()

        response = mgr.read_steer()
        assert response.text == "Approve the approach"

        mgr.resolve("Approved")
        assert not mgr.steer_path.exists()


class TestGracefulShutdown:
    def test_10_step_sequence(self, project: Path):
        transport = TmuxTransport(TmuxSession(session_name="test", dry_run=True))
        shutdown = GracefulShutdown(project / ".zf", transport)
        steps = shutdown.execute()
        # Phase 2.5: added kill_watcher step between kill_session and
        # release_lock so zf stop doesn't leave stale --foreground
        # processes behind. Event index flush appended later for the
        # in-process index introduced alongside the events.jsonl
        # causation projection. stale_inflight_cleanup added by
        # TR-ZF-STOP-GRACEFUL-CLEANUP-001 (task.requeued for stale
        # in-flight WIP on graceful stop). preserve_run_manager added so
        # scoped stop can keep the resident monitor alive.
        assert len(steps) == 14
        assert "shutdown_marker" in steps
        assert "kill_watcher" in steps
        assert "stale_inflight_cleanup" in steps
        assert "preserve_run_manager" in steps
        assert "release_lock" in steps
        assert "flush_event_index" in steps

    def test_step5_writes_role_logs_via_transport(self, project: Path):
        from zf.core.config.loader import load_config
        captured: dict[str, str] = {}

        class RecordingTransport(TmuxTransport):
            def capture_log(self, role_name, lines=200):
                captured[role_name] = f"<{role_name} fake log>"
                return captured[role_name]

        transport = RecordingTransport(TmuxSession(session_name="t", dry_run=True))
        shutdown = GracefulShutdown(project / ".zf", transport, config=load_config(project / "zf.yaml"))
        shutdown.execute()
        # Logs dir should contain a file per non-orchestrator role
        log_files = list((project / ".zf" / "logs").glob("*.log"))
        assert log_files, "step 5 did not write any role log files"
        # And capture_log was actually called
        assert captured, "step 5 did not invoke transport.capture_log"

    def test_step7_creates_last_shutdown_snapshot(self, project: Path):
        """Step 7 writes to .zf/last-shutdown/ (replaces Checkpoint path)."""
        transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
        GracefulShutdown(project / ".zf", transport).execute()
        snapshot = project / ".zf" / "last-shutdown"
        assert snapshot.exists(), "step 7 did not create last-shutdown snapshot"
        assert (snapshot / "snapshot_at").exists()

    def test_step6_snapshots_memory_dir(self, project: Path):
        # Pre-populate a memory file
        mem_dir = project / ".zf" / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "shared.md").write_text("# shared\n")
        transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
        GracefulShutdown(project / ".zf", transport).execute()
        # Memory snapshot now lives under .zf/last-shutdown/memory/
        snapshot = project / ".zf" / "last-shutdown"
        assert snapshot.exists()
        assert (snapshot / "memory").exists() or (snapshot / "memory_snapshot.txt").exists()


class TestColdStart:
    def test_passes_with_valid_project(self, project: Path):
        config = load_config(project / "zf.yaml")
        result = cold_start_check(project, config)
        assert result.score >= 4  # at least 4/5

    def test_fails_without_docs(self, tmp_path: Path):
        from zf.core.config.schema import ZfConfig, ProjectConfig
        config = ZfConfig(project=ProjectConfig(name="test"))
        result = cold_start_check(tmp_path, config)
        assert result.score < 5


class TestHandoff:
    def test_handoff_generates(self, project: Path, capsys):
        main(["kanban", "add", "Task A"])
        result = main(["handoff"])
        assert result == 0
        captured = capsys.readouterr()
        assert "Handoff" in captured.out
        assert "Task A" in captured.out

    def test_handoff_json(self, project: Path, capsys):
        result = main(["handoff", "--format", "json"])
        assert result == 0
        import json
        output = capsys.readouterr().out
        data = json.loads(output)
        assert "done" in data
