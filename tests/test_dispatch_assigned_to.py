"""Tests for G1 + G2: dispatch honors task.assigned_to and Layer 2 mode."""

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
    EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


class _LegacyConfig:
    """No orchestrator role — Python state machine drives dispatch."""

    @staticmethod
    def make() -> ZfConfig:
        return ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(name="dev", backend="mock"),
                RoleConfig(name="arch", backend="mock"),
                RoleConfig(name="review", backend="mock"),
            ],
        )


class _Layer2Config:
    """Has an orchestrator role — Layer 2 drives decisions."""

    @staticmethod
    def make() -> ZfConfig:
        return ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="orchestrator",
                    backend="claude-code",
                    transport="stream-json",
                    triggers=["user.message"],
                ),
                RoleConfig(name="dev", backend="mock"),
                RoleConfig(name="arch", backend="mock"),
                RoleConfig(name="review", backend="mock"),
            ],
        )


# -- G1: _find_available_role honors task.assigned_to --

class TestG1AssignedToHonored:
    def test_assigned_task_dispatches_to_named_role(self, state_dir, transport):
        """If task.assigned_to='arch', dispatch MUST go to arch — not to
        the first role in config.roles (which is 'dev')."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="design auth", assigned_to="arch"))

        orch = Orchestrator(state_dir, _LegacyConfig.make(), transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) == 1
        assert dispatch[0].role == "arch"
        task = store.get("T1")
        assert task.assigned_to == "arch"
        assert task.status == "in_progress"

    def test_unassigned_task_falls_back_to_first_available(
        self, state_dir, transport
    ):
        """Backward compat: if assigned_to is empty, dispatch to first
        available role (current behavior). Must NOT break legacy configs."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x"))  # no assigned_to

        orch = Orchestrator(state_dir, _LegacyConfig.make(), transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) == 1
        # First non-orchestrator role wins (dev)
        assert dispatch[0].role == "dev"

    def test_assigned_to_missing_role_does_not_dispatch(
        self, state_dir, transport
    ):
        """If assigned_to names a role that doesn't exist, don't dispatch
        (don't fall back to first-available — that would be silent data loss)."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="ghost"))

        orch = Orchestrator(state_dir, _LegacyConfig.make(), transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) == 0
        assert store.get("T1").status == "backlog"

    def test_assigned_to_orchestrator_rejected(self, state_dir, transport):
        """Cannot assign tasks to orchestrator role (Layer 2 never executes
        tasks itself — it only dispatches)."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="orchestrator"))

        orch = Orchestrator(state_dir, _Layer2Config.make(), transport)
        orch.run_once()

        assert store.get("T1").status == "backlog"  # not dispatched


# -- G2: Layer 2 mode requires explicit assignment --

class TestG2Layer2Dispatch:
    def test_layer2_mode_skips_unassigned_tasks(self, state_dir, transport):
        """In Layer 2 mode, Python should NOT auto-dispatch unassigned
        backlog tasks. Wait for Layer 2 to call `zf kanban assign`."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x"))  # unassigned

        orch = Orchestrator(state_dir, _Layer2Config.make(), transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) == 0
        assert store.get("T1").status == "backlog"

    def test_layer2_mode_dispatches_assigned_task(self, state_dir, transport):
        """Once Layer 2 has assigned a task, Python dispatches it normally."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="design", assigned_to="arch"))

        orch = Orchestrator(state_dir, _Layer2Config.make(), transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) == 1
        assert dispatch[0].role == "arch"

    def test_legacy_mode_auto_dispatches_unassigned(self, state_dir, transport):
        """No orchestrator role → legacy behavior: auto-dispatch to first role."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x"))  # unassigned

        orch = Orchestrator(state_dir, _LegacyConfig.make(), transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) == 1
