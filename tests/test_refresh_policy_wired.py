"""Tests for G-WIRE-3: RefreshPolicy wired as observation layer.

RefreshPolicy composes 5 trigger types (turns / failures / drift /
context_pressure / task_complete) into a single worker.refresh.triggered
event for observability. Does NOT auto-act — Sprint A's stuck path and
Sprint E's recycle path own the actions.
"""

from __future__ import annotations

import inspect
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


class TestImportProof:
    def test_refresh_policy_imported_by_orchestrator(self):
        from zf.runtime import orchestrator
        src = inspect.getsource(orchestrator)
        assert "RefreshPolicy" in src


class TestInstantiation:
    def test_refresh_policy_created_in_init(self, state_dir, config, transport):
        orch = Orchestrator(state_dir, config, transport)
        assert hasattr(orch, "_refresh_policy")
        assert orch._refresh_policy is not None

    def test_per_instance_counters_initialized_empty(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        assert orch._turn_counter == {}
        assert orch._failure_counter == {}


class TestTurnCounting:
    def test_test_passed_increments_turn_counter(
        self, state_dir, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="dev"))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        assert orch._turn_counter.get("dev", 0) >= 1


class TestFailureCounting:
    def test_review_rejected_increments_failure_counter(
        self, state_dir, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="review", assigned_to="dev"))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="review.rejected", actor="review", task_id="T1"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        assert orch._failure_counter.get("dev", 0) >= 1

    def test_test_passed_resets_failure_counter(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._failure_counter["dev"] = 2

        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="dev"))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="test.passed", actor="test", task_id="T1"))

        orch.run_once()

        assert orch._failure_counter.get("dev", 0) == 0


class TestRefreshTriggered:
    def test_consecutive_failures_emits_refresh(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        # Force failure counter past threshold
        orch._refresh_policy.max_failures = 2
        orch._failure_counter["dev"] = 3

        orch._check_refresh_triggers()

        events = EventLog(state_dir / "events.jsonl").read_all()
        triggers = [e for e in events if e.type == "worker.refresh.triggered"]
        assert any(
            t.payload.get("reason") == "failures" for t in triggers
        )

    def test_turns_elapsed_emits_refresh(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._refresh_policy.max_turns = 5
        orch._turn_counter["dev"] = 10

        orch._check_refresh_triggers()

        events = EventLog(state_dir / "events.jsonl").read_all()
        triggers = [e for e in events if e.type == "worker.refresh.triggered"]
        assert any(
            t.payload.get("reason") == "turns_elapsed" for t in triggers
        )

    def test_dedup_same_reason_within_run(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._refresh_policy.max_failures = 1
        orch._failure_counter["dev"] = 5

        orch._check_refresh_triggers()
        orch._check_refresh_triggers()  # second call → no new emit

        events = EventLog(state_dir / "events.jsonl").read_all()
        triggers = [
            e for e in events
            if e.type == "worker.refresh.triggered"
            and e.actor == "dev"
            and e.payload.get("reason") == "failures"
        ]
        assert len(triggers) == 1

    def test_no_trigger_when_all_metrics_clean(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        # All counters empty, no drift, no pressure
        orch._check_refresh_triggers()

        events = EventLog(state_dir / "events.jsonl").read_all()
        triggers = [e for e in events if e.type == "worker.refresh.triggered"]
        assert triggers == []

    def test_low_node_skip_does_not_trigger_refresh(
        self, state_dir, config, transport,
    ):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="worker.drift.detected",
            actor="zf-cli",
            payload={
                "signal": "node_skip",
                "severity": "low",
                "detail": "Role 'review' not active in recent events",
                "recommended_action": "observe",
                "affected_role": "review",
            },
        ))
        orch = Orchestrator(state_dir, config, transport)

        orch._check_refresh_triggers()

        events = log.read_all()
        triggers = [
            e for e in events
            if e.type == "worker.refresh.triggered"
            and e.payload.get("reason") == "drift"
        ]
        assert triggers == []

    def test_actionable_role_scoped_drift_refreshes_only_affected_worker(
        self, state_dir, config, transport,
    ):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="worker.drift.detected",
            actor="zf-cli",
            payload={
                "signal": "repeat_decisions",
                "severity": "medium",
                "detail": "review repeated the same decision",
                "recommended_action": "refresh",
                "affected_role": "review",
            },
        ))
        orch = Orchestrator(state_dir, config, transport)

        orch._check_refresh_triggers()

        triggers = [
            e for e in log.read_all()
            if e.type == "worker.refresh.triggered"
            and e.payload.get("reason") == "drift"
        ]
        assert [event.actor for event in triggers] == ["review"]


class TestRunOnceIntegration:
    def test_run_once_calls_check_refresh_triggers(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._refresh_policy.max_failures = 1
        orch._failure_counter["dev"] = 5

        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        triggers = [e for e in events if e.type == "worker.refresh.triggered"]
        assert len(triggers) >= 1
