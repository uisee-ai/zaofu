"""Phase 2 integration test — stability features."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.main import main
from zf.core.cost.tracker import CostTracker
from zf.core.statemachine.worker import WorkerStateMachine
from zf.core.statemachine.session import SessionStateMachine
from zf.core.statemachine.loop import LoopStateMachine
from zf.core.statemachine.task import InvalidTransition
from zf.runtime.drift import DriftDetector
from zf.runtime.refresh import RefreshPolicy
from zf.runtime.watcher import StuckDetector


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "phase2-test", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


class TestPhase2WorkerLifecycle:
    def test_full_worker_lifecycle(self):
        sm = WorkerStateMachine()
        state = "idle"
        state = sm.transition(state, "working")
        assert state == "working"
        state = sm.transition(state, "refreshing")
        assert state == "refreshing"
        state = sm.transition(state, "working")
        assert state == "working"
        state = sm.transition(state, "idle")
        assert state == "idle"

    def test_crash_and_recovery(self):
        sm = WorkerStateMachine()
        state = sm.transition("working", "crashed")
        state = sm.transition(state, "idle")
        state = sm.transition(state, "working")
        assert state == "working"


class TestPhase2DriftAndRefresh:
    def test_drift_triggers_refresh(self):
        detector = DriftDetector(thrash_threshold=3)
        events = [{"type": "review.rejected", "task_id": "T1"}] * 4
        signals = detector.check(events)
        assert any(s.signal == "thrashing" for s in signals)

        policy = RefreshPolicy()
        trigger = policy.evaluate(drift_detected=True)
        assert trigger is not None
        assert trigger.reason == "drift"


class TestPhase2CostTracking:
    def test_cost_tracks_and_enforces(self, project: Path):
        tracker = CostTracker(project / ".zf" / "cost.jsonl")
        tracker.record_usage("dev", 500_000, 100_000)
        tracker.record_usage("review", 200_000, 50_000)

        totals = tracker.per_role_totals()
        assert len(totals) == 2

        assert tracker.check_budget(100.0) is True
        assert tracker.check_budget(0.001) is False


class TestPhase2StuckDetection:
    def test_stuck_detector_fires(self):
        import time
        detector = StuckDetector(stale_threshold=0.05)
        detector.update("same output")
        time.sleep(0.06)
        detector.update("same output")
        assert detector.is_stuck()

        detector.update("changed!")
        assert not detector.is_stuck()


class TestPhase2SessionAndLoopSM:
    def test_session_lifecycle(self):
        sm = SessionStateMachine()
        state = sm.transition("created", "active")
        state = sm.transition(state, "degraded")
        state = sm.transition(state, "active")
        state = sm.transition(state, "shutdown_requested")
        state = sm.transition(state, "stopped")
        assert state == "stopped"

    def test_loop_lifecycle(self):
        sm = LoopStateMachine()
        state = sm.transition("starting", "running")
        state = sm.transition(state, "waiting")
        state = sm.transition(state, "running")
        state = sm.transition(state, "completed")
        assert state == "completed"

    def test_loop_failure_recovery(self):
        sm = LoopStateMachine()
        state = sm.transition("starting", "running")
        state = sm.transition(state, "failed")
        state = sm.transition(state, "recovering")
        state = sm.transition(state, "running")
        assert state == "running"
