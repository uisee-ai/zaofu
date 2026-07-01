"""End-to-end tests for Sprint F: 3 dead components + grep-proof."""

from __future__ import annotations

import inspect
import os
import time
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path
    (ws / "src").mkdir()
    (ws / "src" / "auth.py").write_text("# auth\n")
    (ws / "src" / "billing.py").write_text("# billing\n")
    return ws


@pytest.fixture
def state_dir(workspace: Path) -> Path:
    sd = workspace / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(workspace))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", backend="mock"),
            RoleConfig(name="review", backend="mock"),
            RoleConfig(name="test", backend="mock"),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


class TestGrepProofs:
    """Wire-up discipline acceptance criterion: every component in
    Sprint F must be referenced from orchestrator.py."""

    def test_all_three_components_imported_by_orchestrator(self):
        from zf.runtime import orchestrator
        src = inspect.getsource(orchestrator)
        for symbol in ("ScopeRatchet", "DriftDetector", "RefreshPolicy"):
            assert symbol in src, (
                f"{symbol} must be imported and used by orchestrator.py "
                f"(library-without-callers anti-pattern check)"
            )


class TestScopeViolationFullChain:
    def test_dispatch_then_out_of_scope_change_emits_violation(
        self, state_dir, workspace, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="auth work", assigned_to="dev",
            contract=TaskContract(scope=["src/auth.py"]),
        ))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()  # dispatch + snapshot

        # Simulate dev modifying a file outside scope
        time.sleep(0.01)
        (workspace / "src" / "billing.py").write_text("# OOPS\n")
        os.utime(workspace / "src" / "billing.py", None)

        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(type="dev.build.done", actor="dev", task_id="T1"),
        )
        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        violations = [e for e in events if e.type == "scope.violation"]
        assert len(violations) >= 1
        all_paths: list[str] = []
        for v in violations:
            paths = v.payload.get("paths") or [v.payload.get("path", "")]
            all_paths.extend(paths)
        assert any("billing.py" in p for p in all_paths)


class TestDriftThrashingFullChain:
    def test_three_review_rejections_emit_thrashing(
        self, state_dir, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="review", assigned_to="dev"))

        log = EventLog(state_dir / "events.jsonl")
        for _ in range(3):
            log.append(ZfEvent(
                type="review.rejected", actor="review", task_id="T1",
            ))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        events = log.read_all()
        drifts = [
            e for e in events
            if e.type == "worker.drift.detected"
            and e.payload.get("signal") == "thrashing"
        ]
        assert len(drifts) >= 1


class TestRefreshAfterMaxTurns:
    def test_turns_elapsed_emits_refresh_triggered(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._refresh_policy.max_turns = 3
        # Pretend dev finished 5 tasks
        orch._turn_counter["dev"] = 5

        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        triggers = [
            e for e in events
            if e.type == "worker.refresh.triggered"
            and e.payload.get("reason") == "turns_elapsed"
        ]
        assert any(t.actor == "dev" for t in triggers)


class TestAllThreeWiredInSameOrchestrator:
    """Smoke check: instantiating a single Orchestrator should set up
    all three observation systems without error, and each should run
    cleanly on a no-op event loop."""

    def test_clean_run_once_does_not_crash_with_all_three_systems(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        # All three subsystems should exist
        assert orch._scope_ratchet is not None
        assert orch._drift_detector is not None
        assert orch._refresh_policy is not None
        # Clean run_once on a fresh state_dir is a no-op for all three
        decisions = orch.run_once()
        assert isinstance(decisions, list)
