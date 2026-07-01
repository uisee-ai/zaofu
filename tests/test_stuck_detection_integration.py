"""Tests for G-LIFE-3: StuckDetector integration into Orchestrator.

Previously StuckDetector was defined but never instantiated in runtime.
This sprint wires it into _capture_logs so workers that stop producing
output trip a worker.stuck event and escalate to human.
"""

from __future__ import annotations

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
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_types import OrchestratorDecision
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _assign_busy_task(state_dir: Path, worker_id: str) -> None:
    """Helper: put a task in_progress assigned to worker_id so the
    stuck detector sees the worker as busy (E2: idle workers are
    exempt from stuck checks)."""
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        title=f"active-{worker_id}",
        status="in_progress",
        assigned_to=worker_id,
    ))


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def legacy_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", backend="mock", stuck_threshold_seconds=0.05),
            RoleConfig(name="review", backend="mock", stuck_threshold_seconds=0.05),
        ],
    )


class _FakeTransport(TmuxTransport):
    """Transport that returns scripted capture_log output per role."""

    def __init__(self, outputs_by_role: dict[str, list[str]]):
        super().__init__(TmuxSession(session_name="t", dry_run=True))
        self._scripts = {k: list(v) for k, v in outputs_by_role.items()}

    def capture_log(self, role: str, lines: int = 200) -> str:
        script = self._scripts.get(role, [])
        if not script:
            return ""
        # Pop the next scripted output; if only one left, return it forever
        if len(script) == 1:
            return script[0]
        return script.pop(0)


class TestRoleConfigStuckThreshold:
    def test_roleconfig_has_stuck_threshold_field(self):
        role = RoleConfig(name="dev")
        assert hasattr(role, "stuck_threshold_seconds")
        assert role.stuck_threshold_seconds == 300.0  # default 5 min

    def test_roleconfig_explicit_threshold(self):
        role = RoleConfig(name="dev", stuck_threshold_seconds=60.0)
        assert role.stuck_threshold_seconds == 60.0


class TestStuckDetectorWired:
    def test_detectors_created_per_worker_role(
        self, state_dir: Path, legacy_config
    ):
        transport = _FakeTransport({"dev": ["init"], "review": ["init"]})
        orch = Orchestrator(state_dir, legacy_config, transport)
        # Orchestrator should maintain a dict of detectors keyed by role name
        assert hasattr(orch, "_stuck_detectors")
        assert "dev" in orch._stuck_detectors
        assert "review" in orch._stuck_detectors

    def test_no_detector_for_orchestrator_role(
        self, state_dir: Path, tmp_path: Path
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(name="orchestrator", backend="mock"),
                RoleConfig(name="dev", backend="mock"),
            ],
        )
        transport = _FakeTransport({"dev": ["init"]})
        orch = Orchestrator(state_dir, cfg, transport)
        assert "orchestrator" not in orch._stuck_detectors
        assert "dev" in orch._stuck_detectors


class TestStuckDetection:
    def test_unchanged_output_emits_worker_stuck_after_threshold(
        self, state_dir: Path, legacy_config
    ):
        # E2: worker must be BUSY (in_progress task assigned) for the
        # stuck detector to fire. Idle workers whose pane output is
        # unchanged are legitimately waiting and must be exempt.
        _assign_busy_task(state_dir, "dev")
        transport = _FakeTransport({
            "dev": ["frozen output"],
            "review": ["reviewing..."],
        })
        orch = Orchestrator(state_dir, legacy_config, transport)

        # First run: seeds the detector (output captured, not stuck yet)
        orch.run_once()

        # Wait past the tiny threshold
        time.sleep(0.1)

        # Second run: same output → should trip worker.stuck
        orch.run_once()

        log = EventLog(state_dir / "events.jsonl")
        events = log.read_all()
        stuck_events = [e for e in events if e.type == "worker.stuck"]
        assert len(stuck_events) >= 1, f"expected worker.stuck, got types: {[e.type for e in events]}"
        assert any(e.actor == "dev" for e in stuck_events)

    def test_stuck_event_carries_task_and_dispatch_context(
        self, state_dir: Path, legacy_config
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T-STUCK",
            title="active",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="disp-stuck",
        ))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id="T-STUCK",
            payload={
                "assignee": "dev",
                "role": "dev",
                "dispatch_id": "disp-stuck",
                "briefing": "/tmp/T-STUCK.md",
            },
        ))
        transport = _FakeTransport({
            "dev": ["frozen output"],
            "review": ["reviewing..."],
        })
        orch = Orchestrator(state_dir, legacy_config, transport)

        orch.run_once()
        time.sleep(0.1)
        orch.run_once()

        stuck = [
            e for e in log.read_all()
            if e.type == "worker.stuck" and e.actor == "dev"
        ][-1]
        assert stuck.task_id == "T-STUCK"
        assert stuck.payload["task_id"] == "T-STUCK"
        assert stuck.payload["dispatch_id"] == "disp-stuck"
        assert stuck.payload["briefing"] == "/tmp/T-STUCK.md"

    def test_stuck_event_triggers_escalate(
        self, state_dir: Path, legacy_config
    ):
        _assign_busy_task(state_dir, "dev")
        transport = _FakeTransport({
            "dev": ["same"],
            "review": ["still reviewing"],
        })
        orch = Orchestrator(state_dir, legacy_config, transport)

        def fail_respawn(role):  # noqa: ANN001
            return OrchestratorDecision(
                action="respawn_failed",
                role=role.instance_id,
                reason="forced test failure",
            )

        orch._respawn_instance = fail_respawn  # type: ignore[method-assign]
        orch.run_once()
        time.sleep(0.1)
        orch.run_once()

        # Must land human.escalate
        events = EventLog(state_dir / "events.jsonl").read_all()
        escalates = [e for e in events if e.type == "human.escalate"]
        assert len(escalates) >= 1
        # Reason should mention stuck + role
        assert any("dev" in (e.payload.get("reason", "") or "") for e in escalates)

    def test_idle_worker_not_flagged_stuck(
        self, state_dir: Path, legacy_config
    ):
        """E2 regression: worker with no in_progress task assigned must
        NOT trip worker.stuck even if its pane output is unchanged for
        longer than stuck_threshold_seconds. Run 15 produced 6 false
        worker.stuck events this way (all workers idle between
        pipeline handoffs)."""
        transport = _FakeTransport({
            "dev": ["idle prompt"],
            "review": ["idle prompt"],
        })
        orch = Orchestrator(state_dir, legacy_config, transport)
        # NOTE: no task assigned → both workers are idle.

        orch.run_once()
        time.sleep(0.1)
        orch.run_once()
        time.sleep(0.1)
        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        stuck = [e for e in events if e.type == "worker.stuck"]
        assert not stuck, (
            f"idle worker wrongly flagged stuck: "
            f"{[(e.actor, e.payload) for e in stuck]}"
        )

    def test_idle_then_busy_detector_starts_fresh(
        self, state_dir: Path, legacy_config
    ):
        """E2: when an idle worker receives a task, the stuck clock
        must reset — the detector shouldn't carry over the old
        'unchanged' time from the idle period."""
        transport = _FakeTransport({
            # Same output throughout: if the detector kept its clock
            # from the idle phase, it'd trip immediately after assign.
            "dev": ["prompt>"],
            "review": ["prompt>"],
        })
        orch = Orchestrator(state_dir, legacy_config, transport)

        # Phase 1: idle — detector must reset each cycle.
        orch.run_once()
        time.sleep(0.1)
        orch.run_once()

        # Phase 2: task arrives, worker now busy. Next cycle starts the
        # stale clock fresh; stuck should NOT fire on the very next
        # tick just because the idle phase was long.
        _assign_busy_task(state_dir, "dev")
        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        stuck_on_first_busy_tick = [
            e for e in events if e.type == "worker.stuck"
        ]
        assert not stuck_on_first_busy_tick, (
            "detector carried stale clock from idle phase"
        )

    def test_active_worker_not_flagged(
        self, state_dir: Path, legacy_config
    ):
        """Worker that produces new output each cycle must NOT be flagged."""
        transport = _FakeTransport({
            "dev": ["output v1", "output v2", "output v3"],
            "review": ["r1", "r2", "r3"],
        })
        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()
        time.sleep(0.1)
        orch.run_once()
        time.sleep(0.1)
        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not any(e.type == "worker.stuck" for e in events)

    def test_empty_output_does_not_trip_detector(
        self, state_dir: Path, legacy_config
    ):
        """If transport returns '' (pane not ready yet), don't flag as stuck."""
        transport = _FakeTransport({"dev": [""], "review": [""]})
        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()
        time.sleep(0.1)
        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not any(e.type == "worker.stuck" for e in events)


class TestWakePatternsIncludeStuckEvent:
    def test_worker_stuck_in_wake_patterns(self):
        """wake_patterns must include worker.stuck so the watcher
        triggers run_once on the self-emitted stuck event."""
        from zf.runtime.wake_patterns import WAKE_PATTERNS
        assert "worker.stuck" in WAKE_PATTERNS
