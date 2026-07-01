"""Tests for G-LIFE-2: EscalationManager wired into Orchestrator.

The EscalationManager class (escalation.py) is complete but was never
instantiated by runtime code. This sprint wires it into Orchestrator
and triggers it from _on_dev_blocked.
"""

from __future__ import annotations

import json
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
from zf.runtime.escalation import EscalationManager
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
def legacy_config() -> ZfConfig:
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


class TestEscalationInstance:
    def test_orchestrator_has_escalation_manager(
        self, state_dir: Path, legacy_config, transport
    ):
        orch = Orchestrator(state_dir, legacy_config, transport)
        assert hasattr(orch, "escalation")
        assert isinstance(orch.escalation, EscalationManager)

    def test_escalation_points_at_state_dir(
        self, state_dir: Path, legacy_config, transport
    ):
        orch = Orchestrator(state_dir, legacy_config, transport)
        assert orch.escalation.state_dir == state_dir


class TestDevBlockedTriggersEscalate:
    def test_dev_blocked_emits_human_escalate_event(
        self, state_dir: Path, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="build auth", status="in_progress",
                       assigned_to="dev"))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="dev.blocked", actor="dev", task_id="T1",
            payload={"reason": "missing API key for OAuth provider"},
        ))

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        all_events = log.read_all()
        escalate_events = [e for e in all_events if e.type == "human.escalate"]
        assert len(escalate_events) >= 1
        ev = escalate_events[-1]
        assert ev.task_id == "T1"
        assert "OAuth" in ev.payload.get("reason", "")

    def test_dev_blocked_writes_steer_marker(
        self, state_dir: Path, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="in_progress",
                       assigned_to="dev"))

        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="dev.blocked", actor="dev", task_id="T1",
            payload={"reason": "external dep down"},
        ))

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        steer = state_dir / "steer"
        assert steer.exists(), "steer marker should be created on escalate"

    def test_dev_blocked_without_reason_still_escalates(
        self, state_dir: Path, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="in_progress",
                       assigned_to="dev"))

        EventLog(state_dir / "events.jsonl").append(
            ZfEvent(type="dev.blocked", actor="dev", task_id="T1")
        )

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        # Must not crash; escalation event should still land
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(e.type == "human.escalate" for e in events)


class TestHumanEscalateObservationalHandler:
    """0608: Layer 1 records human.escalate observationally; Layer 2 (woken
    via WAKE_PATTERNS) owns the autonomous follow-up action."""

    def test_human_escalate_registered_and_in_wake_patterns(self):
        from zf.runtime.orchestrator_reactor import _BUILTIN_HANDLER_METHODS
        from zf.runtime.wake_patterns import WAKE_PATTERNS

        registered = dict(_BUILTIN_HANDLER_METHODS)
        assert registered.get("human.escalate") == "_on_human_escalate"
        assert "human.escalate" in WAKE_PATTERNS

    def test_human_escalate_handler_is_observational(
        self, state_dir: Path, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="blocked",
                       assigned_to="dev"))

        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="human.escalate", actor="zf-cli", task_id="T1",
            payload={"reason": "retry cap exceeded"},
        ))

        orch = Orchestrator(state_dir, legacy_config, transport)
        # Layer 1 handler is observational — processing must not raise.
        orch.run_once()

        task = TaskStore(state_dir / "kanban.json").get("T1")
        assert task is not None
        # The observational handler does not move the task on its own.
        assert task.status == "blocked"
