"""Tests for G-WIRE-2: DriftDetector wired into orchestrator runtime."""

from __future__ import annotations

import inspect
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
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


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
def config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", backend="mock"),
            RoleConfig(name="review", backend="mock"),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def _emit(state_dir: Path, *events: ZfEvent) -> None:
    log = EventLog(state_dir / "events.jsonl")
    for e in events:
        log.append(e)


class TestImportProof:
    def test_drift_detector_imported_by_orchestrator(self):
        from zf.runtime import orchestrator
        src = inspect.getsource(orchestrator)
        assert "DriftDetector" in src


class TestInstantiation:
    def test_drift_detector_created_in_init(self, state_dir, config, transport):
        orch = Orchestrator(state_dir, config, transport)
        assert hasattr(orch, "_drift_detector")
        assert orch._drift_detector is not None


class TestThrashingDetection:
    def test_three_review_rejections_emit_thrashing_signal(
        self, state_dir, config, transport
    ):
        # Seed a task and 3 review.rejected events for it
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="x", status="review", assigned_to="review"),
        )
        for _ in range(3):
            _emit(state_dir, ZfEvent(
                type="review.rejected", actor="review", task_id="T1",
            ))

        orch = Orchestrator(state_dir, config, transport)
        orch._check_drift()

        events = EventLog(state_dir / "events.jsonl").read_all()
        drift = [e for e in events if e.type == "worker.drift.detected"]
        assert len(drift) >= 1
        assert any(
            e.payload.get("signal") == "thrashing" for e in drift
        )


class TestRepeatDecisionsDetection:
    def test_repeat_event_type_emits_repeat_decisions(
        self, state_dir, config, transport
    ):
        # Many of the same event type
        for _ in range(8):
            _emit(state_dir, ZfEvent(type="task.dispatched", actor="orchestrator"))

        orch = Orchestrator(state_dir, config, transport)
        orch._check_drift()

        events = EventLog(state_dir / "events.jsonl").read_all()
        drift = [e for e in events if e.type == "worker.drift.detected"]
        assert any(
            e.payload.get("signal") == "repeat_decisions" for e in drift
        )

    def test_finished_writer_excluded_from_drift_expected_roles(
        self, state_dir, config, transport
    ):
        """R18: a writer with an in_progress task that already emitted
        dev.build.done (awaiting review/integration) is legitimately idle and
        must NOT be an 'expected active' role — else node-skip drift fires
        perpetually for it (144× 'Role dev-lane-X not active in recent events').
        Same exemption as the completed-writer stuck fix (0886466 / 82f0feb)."""
        TaskStore(state_dir / "kanban.json").add(Task(
            id="T1", title="x", status="in_progress",
            assigned_to="dev", active_dispatch_id="disp-1",
        ))
        _emit(state_dir, ZfEvent(
            type="task.dispatched", actor="orchestrator", task_id="T1",
            payload={"dispatch_id": "disp-1", "role": "dev"},
        ))
        orch = Orchestrator(state_dir, config, transport)
        # before completion: the writer is genuinely expected to be active
        assert "dev" in orch._active_drift_expected_roles()
        # the writer finishes its build → now legitimately idle in later stages
        _emit(state_dir, ZfEvent(
            type="dev.build.done", actor="dev", task_id="T1", payload={},
        ))
        assert "dev" not in orch._active_drift_expected_roles()

    def test_inactive_configured_roles_do_not_emit_node_skip(
        self, state_dir, config, transport
    ):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="x", status="in_progress", assigned_to="dev"),
        )
        for i in range(12):
            _emit(
                state_dir,
                ZfEvent(type=f"dev.progress.{i}", actor="dev-1", task_id="T1"),
            )

        orch = Orchestrator(state_dir, config, transport)
        orch._check_drift()

        events = EventLog(state_dir / "events.jsonl").read_all()
        drift = [e for e in events if e.type == "worker.drift.detected"]
        assert not any(e.payload.get("signal") == "node_skip" for e in drift)

    def test_agent_usage_counts_as_activity_for_node_skip(
        self, state_dir, config, transport
    ):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="x", status="in_progress", assigned_to="review"),
        )
        for _ in range(12):
            _emit(state_dir, ZfEvent(type="agent.usage", actor="review"))

        orch = Orchestrator(state_dir, config, transport)
        orch._check_drift()

        events = EventLog(state_dir / "events.jsonl").read_all()
        drift = [e for e in events if e.type == "worker.drift.detected"]
        assert not any(e.payload.get("signal") == "node_skip" for e in drift)

    def test_node_skip_emits_affected_role(
        self, state_dir, config, transport,
    ):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="x", status="in_progress", assigned_to="review"),
        )
        for i in range(12):
            _emit(
                state_dir,
                ZfEvent(type=f"dev.progress.{i}", actor="dev", task_id="T1"),
            )

        orch = Orchestrator(state_dir, config, transport)
        orch._check_drift()

        events = EventLog(state_dir / "events.jsonl").read_all()
        drift = [e for e in events if e.type == "worker.drift.detected"]
        node_skip = [e for e in drift if e.payload.get("signal") == "node_skip"]
        assert node_skip[-1].payload.get("affected_role") == "review"
        assert node_skip[-1].payload.get("recommended_action") == "observe"

    def test_live_provider_turn_suppresses_node_skip(
        self, state_dir, config, transport, monkeypatch,
    ):
        TaskStore(state_dir / "kanban.json").add(Task(
            id="T1",
            title="x",
            status="in_progress",
            assigned_to="review",
            active_dispatch_id="dispatch-live",
        ))
        for i in range(12):
            _emit(
                state_dir,
                ZfEvent(type=f"dev.progress.{i}", actor="dev", task_id="T1"),
            )
        orch = Orchestrator(state_dir, config, transport)
        monkeypatch.setattr(
            orch,
            "_active_provider_turn",
            lambda instance_id: (
                {"age_s": 10.0, "turn_id": "turn-live"}
                if instance_id == "review"
                else None
            ),
        )

        orch._check_drift()

        drift = [
            e for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "worker.drift.detected"
        ]
        assert not any(e.payload.get("signal") == "node_skip" for e in drift)


class TestCooldownDedup:
    def test_same_signal_within_cooldown_not_re_emitted(
        self, state_dir, config, transport
    ):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="x", status="review", assigned_to="review"),
        )
        for _ in range(3):
            _emit(state_dir, ZfEvent(
                type="review.rejected", actor="review", task_id="T1",
            ))

        orch = Orchestrator(state_dir, config, transport)
        orch._check_drift()
        first_count = sum(
            1 for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "worker.drift.detected"
            and e.payload.get("signal") == "thrashing"
        )

        # Re-call immediately — within cooldown
        orch._check_drift()
        second_count = sum(
            1 for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "worker.drift.detected"
            and e.payload.get("signal") == "thrashing"
        )
        assert second_count == first_count  # no new emission

    def test_new_dispatch_gets_independent_node_skip_observation(
        self, state_dir, config, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="in_progress",
            assigned_to="review",
            active_dispatch_id="dispatch-1",
        ))
        for i in range(12):
            _emit(
                state_dir,
                ZfEvent(type=f"dev.progress.{i}", actor="dev", task_id="T1"),
            )
        orch = Orchestrator(state_dir, config, transport)

        orch._check_drift()
        orch._check_drift()
        first = [
            e for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "worker.drift.detected"
            and e.payload.get("signal") == "node_skip"
        ]
        assert len(first) == 1
        assert first[0].payload.get("dispatch_id") == "dispatch-1"

        store.update("T1", active_dispatch_id="dispatch-2")
        orch._check_drift()
        current = [
            e for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "worker.drift.detected"
            and e.payload.get("signal") == "node_skip"
        ]
        assert len(current) == 2
        assert current[-1].payload.get("dispatch_id") == "dispatch-2"

    def test_signal_re_emits_after_cooldown(
        self, state_dir, config, transport
    ):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="x", status="review", assigned_to="review"),
        )
        for _ in range(3):
            _emit(state_dir, ZfEvent(
                type="review.rejected", actor="review", task_id="T1",
            ))

        orch = Orchestrator(state_dir, config, transport)
        orch._drift_cooldown_seconds = 0.01  # nearly instant
        orch._check_drift()
        first = sum(
            1 for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "worker.drift.detected"
        )

        time.sleep(0.05)
        orch._check_drift()
        second = sum(
            1 for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "worker.drift.detected"
        )
        assert second > first


class TestNoSpuriousDrift:
    def test_clean_event_log_no_drift(self, state_dir, config, transport):
        # Only loop.started in the log
        orch = Orchestrator(state_dir, config, transport)
        orch._check_drift()
        events = EventLog(state_dir / "events.jsonl").read_all()
        drift = [e for e in events if e.type == "worker.drift.detected"]
        assert drift == []


class TestRunOnceTriggersDriftCheck:
    def test_run_once_calls_drift_check(self, state_dir, config, transport):
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="x", status="review", assigned_to="review"),
        )
        for _ in range(3):
            _emit(state_dir, ZfEvent(
                type="review.rejected", actor="review", task_id="T1",
            ))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(e.type == "worker.drift.detected" for e in events)
