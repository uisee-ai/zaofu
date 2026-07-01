"""Tests for G-GAN-1: GAN loop activates `workflow.gan_rounds`.

When workflow.gan_rounds >= 2 and arch.proposal.done arrives,
orchestrator routes the task back through arch ↔ review for the
configured number of rounds before advancing to dev/test/judge.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
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
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def _make_config(gan_rounds: int) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        workflow=WorkflowConfig(gan_rounds=gan_rounds),
        roles=[
            RoleConfig(name="arch", backend="mock"),
            RoleConfig(name="dev", backend="mock"),
            RoleConfig(name="review", backend="mock"),
            RoleConfig(name="test", backend="mock"),
        ],
    )


def _emit(state_dir: Path, event: ZfEvent) -> None:
    EventLog(state_dir / "events.jsonl").append(event)


class TestGanRounds1Legacy:
    def test_gan_rounds_1_acts_like_legacy_no_loop(
        self, state_dir, transport
    ):
        """gan_rounds=1 means the old behavior: arch.proposal.done →
        review immediately, no loop."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="design", status="in_progress",
                       assigned_to="arch"))

        _emit(state_dir, ZfEvent(
            type="arch.proposal.done", actor="arch", task_id="T1",
        ))

        orch = Orchestrator(state_dir, _make_config(gan_rounds=1), transport)
        orch.run_once()

        assert store.get("T1").status == "review"

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "gan.round.started" not in types
        assert "gan.round.completed" not in types


class TestGanRounds2Loop:
    def test_first_round_does_not_advance_to_review(
        self, state_dir, transport
    ):
        """gan_rounds=2: first arch.proposal.done is round 1/2 — task
        stays in in_progress, gan.round.started fires."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="design", status="in_progress",
                       assigned_to="arch"))

        _emit(state_dir, ZfEvent(
            type="arch.proposal.done", actor="arch", task_id="T1",
        ))

        orch = Orchestrator(state_dir, _make_config(gan_rounds=2), transport)
        orch.run_once()

        # Task NOT in review yet
        assert store.get("T1").status == "in_progress"

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "gan.round.started" in types
        # Not yet completed
        assert "gan.round.completed" not in types

    def test_final_round_advances_to_review(
        self, state_dir, transport
    ):
        """After 2 arch.proposal.done events with gan_rounds=2, task
        finally moves to review."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="design", status="in_progress",
                       assigned_to="arch"))

        orch = Orchestrator(state_dir, _make_config(gan_rounds=2), transport)

        # Round 1
        _emit(state_dir, ZfEvent(
            type="arch.proposal.done", actor="arch", task_id="T1",
        ))
        orch.run_once()
        assert store.get("T1").status == "in_progress"

        # Round 2 (final)
        _emit(state_dir, ZfEvent(
            type="arch.proposal.done", actor="arch", task_id="T1",
        ))
        orch.run_once()
        assert store.get("T1").status == "review"

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "gan.round.completed" in types

    def test_gan_counter_resets_on_done(self, state_dir, transport):
        """After task completes, the per-task gan counter is cleared
        so a re-dispatched task starts fresh."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="design", status="in_progress",
                       assigned_to="arch"))

        orch = Orchestrator(state_dir, _make_config(gan_rounds=2), transport)
        _emit(state_dir, ZfEvent(type="arch.proposal.done",
                                 actor="arch", task_id="T1"))
        orch.run_once()
        assert orch._gan_round.get("T1", 0) == 1

        _emit(state_dir, ZfEvent(type="arch.proposal.done",
                                 actor="arch", task_id="T1"))
        orch.run_once()  # → review

        # Counter cleared on final round (transition to review)
        assert "T1" not in orch._gan_round


class TestDevUnaffected:
    def test_dev_build_done_unaffected_by_gan(self, state_dir, transport):
        """dev.build.done is not subject to GAN loop — it goes straight
        to review regardless of gan_rounds."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="impl", status="in_progress",
                       assigned_to="dev"))

        _emit(state_dir, ZfEvent(
            type="dev.build.done", actor="dev", task_id="T1",
        ))

        orch = Orchestrator(state_dir, _make_config(gan_rounds=3), transport)
        orch.run_once()

        # dev.build.done → review immediately, no GAN
        assert store.get("T1").status == "review"
        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "gan.round.started" not in types
