"""Tests for B3: event-derived per-worker health tracking.

The worker-health read model folds worker.state.changed events; it is not a
claim that all worker/runtime state lives only in the event ledger. The
orchestrator emits worker.state.changed at hook
points in _dispatch_task / _on_build_done / _on_test_passed /
_on_dev_blocked / _report_stuck_worker / _respawn_instance /
_check_context_thresholds / _check_pending_recycles / _start_recycle.

worker_health() folds recent events to return the current state per
instance. zf status --workers renders the same as a table.
"""

from __future__ import annotations

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


class TestSetWorkerStateIdempotent:
    def test_no_op_when_state_unchanged(self, state_dir, config, transport):
        orch = Orchestrator(state_dir, config, transport)
        orch._set_worker_state("dev", "busy", reason="first")
        before_events = len(
            [e for e in EventLog(state_dir / "events.jsonl").read_all()
             if e.type == "worker.state.changed"]
        )
        orch._set_worker_state("dev", "busy", reason="still busy, dup")
        after_events = len(
            [e for e in EventLog(state_dir / "events.jsonl").read_all()
             if e.type == "worker.state.changed"]
        )
        assert after_events == before_events  # no new emission

    def test_transition_emits_event(self, state_dir, config, transport):
        orch = Orchestrator(state_dir, config, transport)
        orch._set_worker_state("dev", "busy", reason="first")
        orch._set_worker_state("dev", "idle", reason="task done")
        events = [
            e for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "worker.state.changed" and e.actor == "dev"
        ]
        assert len(events) == 2
        assert events[0].payload.get("to") == "busy"
        assert events[1].payload.get("from") == "busy"
        assert events[1].payload.get("to") == "idle"

    def test_busy_task_handoff_emits_new_generation(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._set_worker_state("dev", "busy", reason="task one", task_id="T1")
        orch._set_worker_state("dev", "busy", reason="task two", task_id="T2")

        events = [
            event
            for event in EventLog(state_dir / "events.jsonl").read_all()
            if event.type == "worker.state.changed" and event.actor == "dev"
        ]
        assert len(events) == 2
        assert events[-1].payload.get("from") == "busy"
        assert events[-1].payload.get("to") == "busy"
        assert events[-1].task_id == "T2"

    def test_taskless_release_cannot_retire_active_task_generation(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._set_worker_state("dev", "busy", reason="task two", task_id="T2")
        before = len(EventLog(state_dir / "events.jsonl").read_all())

        orch._set_worker_state("dev", "awaiting_review", reason="late task one")

        assert len(EventLog(state_dir / "events.jsonl").read_all()) == before
        assert orch.worker_health()["dev"] == "busy"
        assert orch._last_worker_task_id["dev"] == "T2"

    def test_matching_release_retires_active_task_generation(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._set_worker_state("dev", "busy", reason="task two", task_id="T2")

        orch._set_worker_state(
            "dev",
            "awaiting_review",
            reason="task two complete",
            task_id="T2",
        )

        assert orch.worker_health()["dev"] == "awaiting_review"
        assert "dev" not in orch._last_worker_task_id

    def test_equivalent_task_releases_are_idempotent_without_active_generation(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._set_worker_state(
            "dev", "awaiting_review", reason="task one complete", task_id="T1",
        )
        before = len(EventLog(state_dir / "events.jsonl").read_all())

        orch._set_worker_state(
            "dev", "awaiting_review", reason="task two already complete", task_id="T2",
        )

        assert len(EventLog(state_dir / "events.jsonl").read_all()) == before

    def test_busy_state_restores_missing_task_generation(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._last_worker_state["dev"] = "busy"
        orch._last_worker_task_id.pop("dev", None)

        orch._set_worker_state(
            "dev", "busy", reason="restore active binding", task_id="T2",
        )

        assert orch._last_worker_task_id["dev"] == "T2"
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert events[-1].task_id == "T2"


class TestWorkerHealthFoldsEvents:
    def test_default_state_is_idle(self, state_dir, config, transport):
        orch = Orchestrator(state_dir, config, transport)
        health = orch.worker_health()
        assert health == {"dev": "idle", "review": "idle"}

    def test_latest_event_wins(self, state_dir, config, transport):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="worker.state.changed", actor="dev",
            payload={"from": "idle", "to": "busy", "reason": "r1"},
        ))
        log.append(ZfEvent(
            type="worker.state.changed", actor="dev",
            payload={"from": "busy", "to": "awaiting_review", "reason": "r2"},
        ))
        orch = Orchestrator(state_dir, config, transport)
        health = orch.worker_health()
        assert health["dev"] == "awaiting_review"
        assert health["review"] == "idle"

    def test_survives_restart(self, state_dir, config, transport):
        """Persistence proof: state transitions go into events.jsonl;
        a fresh Orchestrator instance rebuilds the health map from the
        event tail. This is the point of using events as the truth."""
        orch1 = Orchestrator(state_dir, config, transport)
        orch1._set_worker_state("dev", "busy", reason="task T1")
        orch1._set_worker_state("review", "busy", reason="task T2")
        del orch1  # simulate orchestrator crash / restart

        orch2 = Orchestrator(state_dir, config, transport)
        health = orch2.worker_health()
        assert health["dev"] == "busy"
        assert health["review"] == "busy"

    def test_restart_ignores_taskless_stale_release(
        self, state_dir, config, transport
    ):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="worker.state.changed",
            actor="dev",
            task_id="T2",
            payload={"from": "idle", "to": "busy", "task_id": "T2"},
        ))
        log.append(ZfEvent(
            type="worker.state.changed",
            actor="dev",
            payload={"from": "busy", "to": "awaiting_review"},
        ))

        orch = Orchestrator(state_dir, config, transport)

        assert orch.worker_health()["dev"] == "busy"
        assert orch._last_worker_task_id["dev"] == "T2"


class TestDispatchHookSetsWorkerBusy:
    def test_dispatch_emits_worker_state_busy(
        self, state_dir, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev"))
        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        state_events = [
            e for e in events
            if e.type == "worker.state.changed" and e.actor == "dev"
        ]
        assert len(state_events) >= 1
        assert state_events[-1].payload.get("to") == "busy"


class TestOnBuildDoneHookSetsAwaitingReview:
    def test_dev_build_done_emits_awaiting_review(
        self, state_dir, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x",
            status="in_progress", assigned_to="dev",
        ))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))
        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        state_events = [
            e for e in log.read_all()
            if e.type == "worker.state.changed"
            and e.actor == "dev"
        ]
        assert any(
            e.payload.get("to") == "awaiting_review" for e in state_events
        )

    def test_old_completion_does_not_overwrite_new_task_dispatch(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._set_worker_state(
            "dev", "busy", reason="new fanout child", task_id="T2",
        )

        orch._release_stage_actor_liveness(ZfEvent(
            type="dev.build.done",
            actor="dev",
            task_id="T1",
        ))

        assert orch.worker_health()["dev"] == "busy"

    def test_taskless_completion_does_not_overwrite_active_generation(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._set_worker_state(
            "dev", "busy", reason="new fanout child", task_id="T2",
        )

        orch._release_stage_actor_liveness(ZfEvent(
            type="dev.build.done",
            actor="dev",
        ))

        assert orch.worker_health()["dev"] == "busy"

    def test_old_attempt_completion_does_not_release_same_task_rework(
        self, state_dir, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="dispatch-new",
        ))
        orch = Orchestrator(state_dir, config, transport)
        orch._set_worker_state(
            "dev", "busy", reason="same task rework", task_id="T1",
        )

        orch._settle_progress_actor(ZfEvent(
            type="dev.build.done",
            actor="dev",
            task_id="T1",
            payload={"dispatch_id": "dispatch-old"},
        ), "T1")

        assert orch.worker_health()["dev"] == "busy"


class TestOnTestPassedHookSetsIdle:
    def test_test_passed_emits_idle(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x",
            status="testing", assigned_to="dev",
        ))
        log = EventLog(state_dir / "events.jsonl")
        # Simulate dev's prior state — awaiting_review (normal pre-test
        # state after dev.build.done → review.approved → test.started).
        # Without this, "idle" → "idle" is a no-op and the test can't
        # observe the transition.
        log.append(ZfEvent(
            type="worker.state.changed", actor="dev",
            payload={"from": "busy", "to": "awaiting_review", "reason": "prior"},
        ))
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))
        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        state_events = [
            e for e in log.read_all()
            if e.type == "worker.state.changed" and e.actor == "dev"
        ]
        # Final state should be idle (task done)
        assert state_events[-1].payload.get("to") == "idle"


class TestDevBlockedHookSetsBlockedHuman:
    def test_dev_blocked_emits_blocked_human(
        self, state_dir, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x",
            status="in_progress", assigned_to="dev",
        ))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="dev.blocked", actor="dev", task_id="T1",
            payload={"reason": "missing API key"},
        ))
        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        state_events = [
            e for e in log.read_all()
            if e.type == "worker.state.changed" and e.actor == "dev"
        ]
        assert any(
            e.payload.get("to") == "blocked_human" for e in state_events
        )


class TestOrchestratorImportProof:
    def test_set_worker_state_referenced_in_orchestrator(self):
        assert Orchestrator._set_worker_state.__module__ == (
            "zf.runtime.worker_state_runtime"
        )
        assert Orchestrator.worker_health.__module__ == (
            "zf.runtime.worker_state_runtime"
        )


class TestWakePatternIncludesWorkerStateChanged:
    def test_wake_patterns_includes_state_changed(self):
        from zf.runtime.wake_patterns import WAKE_PATTERNS
        assert "worker.state.changed" in WAKE_PATTERNS
