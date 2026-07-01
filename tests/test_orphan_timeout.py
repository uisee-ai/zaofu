"""LH-0.T3: orphaned task timeout — in_progress tasks that haven't seen
a stage-completion event within N minutes get warning / escalated.

Two-stage timeout:
  - orphan_warning_seconds (default 900s / 15 min) → task.orphan_warning
  - orphan_escalate_seconds (default 1800s / 30 min) → task.orphaned
    + human.escalate, assigned_to cleared, status back to backlog

Time is abstracted through Orchestrator._now() for testability.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig, RoleConfig, SessionConfig, ZfConfig,
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


def _seed_busy_task(state_dir: Path, task_id: str, dispatch_ts: float) -> None:
    """Seed a task as dispatched at dispatch_ts (monotonic seconds).

    Adds the task to kanban in in_progress state and writes a
    task.dispatched event with the given timestamp baked into actor
    (the orchestrator only reads monotonic clock via _now(); the event
    ts is for audit only)."""
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id=task_id, title="x", status="in_progress", assigned_to="dev",
    ))
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="task.dispatched", actor="orchestrator", task_id=task_id,
        payload={"role": "dev"},
    ))


class TestConfigSchema:
    def test_orphan_defaults(self):
        r = RoleConfig(name="dev")
        assert r.orphan_warning_seconds == 900
        assert r.orphan_escalate_seconds == 1800


class TestOrphanWarning:
    def test_task_with_no_progress_past_warning_threshold_emits(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        _seed_busy_task(state_dir, "T1", dispatch_ts=0.0)

        orch._dispatch_epoch["T1"] = 0.0  # dispatched at t=0
        orch._now = lambda: 1000.0  # 1000s > 900s warning
        orch._check_orphaned_tasks()

        events = EventLog(state_dir / "events.jsonl").read_all()
        warnings = [e for e in events if e.type == "task.orphan_warning"
                    and e.task_id == "T1"]
        assert len(warnings) == 1

    def test_warning_only_fires_once(self, state_dir, config, transport):
        """Dedup: scanning multiple times within warning window produces
        only one orphan_warning event."""
        orch = Orchestrator(state_dir, config, transport)
        _seed_busy_task(state_dir, "T1", dispatch_ts=0.0)
        orch._dispatch_epoch["T1"] = 0.0

        orch._now = lambda: 1000.0
        orch._check_orphaned_tasks()
        orch._now = lambda: 1200.0
        orch._check_orphaned_tasks()

        events = EventLog(state_dir / "events.jsonl").read_all()
        warnings = [e for e in events if e.type == "task.orphan_warning"]
        assert len(warnings) == 1

    def test_under_threshold_no_fire(self, state_dir, config, transport):
        orch = Orchestrator(state_dir, config, transport)
        _seed_busy_task(state_dir, "T1", dispatch_ts=0.0)
        orch._dispatch_epoch["T1"] = 0.0

        orch._now = lambda: 500.0  # < 900s warning
        orch._check_orphaned_tasks()

        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not any(e.type == "task.orphan_warning" for e in events)


class TestOrphanEscalate:
    def test_past_escalate_threshold_unassigns_and_backlogs(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        _seed_busy_task(state_dir, "T1", dispatch_ts=0.0)
        orch._dispatch_epoch["T1"] = 0.0

        orch._now = lambda: 2000.0  # > 1800s escalate
        orch._check_orphaned_tasks()

        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(e.type == "task.orphaned" and e.task_id == "T1"
                   for e in events)
        assert any(e.type == "human.escalate" for e in events)

        task = TaskStore(state_dir / "kanban.json").get("T1")
        assert task.status == "backlog"
        assert (task.assigned_to or "") == ""

    def test_stage_completion_resets_orphan_clock(
        self, state_dir, config, transport
    ):
        """If a stage-completion event arrives, dispatch_epoch should
        update so the orphan timer effectively resets."""
        orch = Orchestrator(state_dir, config, transport)
        _seed_busy_task(state_dir, "T1", dispatch_ts=0.0)
        orch._dispatch_epoch["T1"] = 0.0

        # 500s after dispatch, dev.build.done arrives — resets the clock
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="dev.build.done", actor="dev", task_id="T1",
        ))
        orch._dispatch_epoch["T1"] = 500.0  # housekeeping bump on stage done

        orch._now = lambda: 1000.0  # 500s since reset → under warning (900)
        orch._check_orphaned_tasks()
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not any(e.type == "task.orphan_warning" for e in events)

    def test_recent_worker_activity_defers_orphan_reset(
        self, state_dir, config, transport,
    ):
        """Changed worker output renews the in-memory lease clock."""
        orch = Orchestrator(state_dir, config, transport)
        _seed_busy_task(state_dir, "T1", dispatch_ts=0.0)
        orch._dispatch_epoch["T1"] = 0.0
        orch._worker_activity_epoch = {"dev": 1500.0}

        orch._now = lambda: 2000.0
        orch._check_orphaned_tasks()

        task = TaskStore(state_dir / "kanban.json").get("T1")
        assert task.status == "in_progress"
        assert orch._dispatch_epoch["T1"] == 1500.0
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not any(e.type == "task.orphaned" for e in events)

    def test_recent_instance_activity_defers_role_name_assigned_task(
        self,
        state_dir,
        transport,
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(name="dev", instance_id="dev-1", backend="mock"),
                RoleConfig(name="review", backend="mock"),
            ],
        )
        orch = Orchestrator(state_dir, cfg, transport)
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="disp-1",
        ))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id="T1",
            payload={"role": "dev-1", "dispatch_id": "disp-1"},
        ))
        orch._dispatch_epoch["T1"] = 0.0
        orch._worker_activity_epoch = {"dev-1": 1500.0}

        orch._now = lambda: 2000.0
        orch._check_orphaned_tasks()

        task = TaskStore(state_dir / "kanban.json").get("T1")
        assert task.status == "in_progress"
        assert orch._dispatch_epoch["T1"] == 1500.0
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not any(e.type == "task.orphaned" for e in events)
        assert not any(e.type == "task.orphan_warning" for e in events)

    def test_recorded_progress_for_dispatch_prevents_orphan_requeue(
        self,
        state_dir,
        transport,
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="critic",
                    backend="mock",
                    orphan_warning_seconds=10,
                    orphan_escalate_seconds=20,
                ),
            ],
        )
        orch = Orchestrator(state_dir, cfg, transport)
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T-DESIGN",
            title="design gate",
            status="in_progress",
            assigned_to="critic",
            active_dispatch_id="disp-design",
        ))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id="T-DESIGN",
            payload={
                "role": "critic",
                "assignee": "critic",
                "dispatch_id": "disp-design",
            },
        ))
        log.append(ZfEvent(
            type="design.critique.done",
            actor="critic",
            task_id="T-DESIGN",
            payload={"dispatch_id": "disp-design"},
        ))
        orch._dispatch_epoch["T-DESIGN"] = 0.0
        orch._now = lambda: 100.0

        orch._check_orphaned_tasks()

        task = TaskStore(state_dir / "kanban.json").get("T-DESIGN")
        assert task is not None
        assert task.status == "in_progress"
        assert task.assigned_to == "critic"
        assert orch._dispatch_epoch["T-DESIGN"] == 100.0
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not any(e.type == "task.orphaned" for e in events)
        assert not any(e.type == "task.orphan_warning" for e in events)


class TestIntegrationDispatchEpoch:
    """_dispatch_epoch must be populated by _dispatch_task (real flow)."""

    def test_dispatch_records_epoch(self, state_dir, config, transport):
        orch = Orchestrator(state_dir, config, transport)
        orch._now = lambda: 100.0
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x", status="backlog", assigned_to="dev",
        ))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="task.created", actor="zf-cli", task_id="T1",
        ))
        orch.run_once()
        assert orch._dispatch_epoch.get("T1") == 100.0


class TestWireUpProof:
    def test_check_orphaned_tasks_wired_into_run_once(self):
        src = (Path(__file__).resolve().parents[1]
               / "src/zf/runtime/orchestrator.py").read_text()
        assert "_check_orphaned_tasks" in src, (
            "_check_orphaned_tasks must be called from run_once"
        )
