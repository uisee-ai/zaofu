"""ZF-E2E-RACING-P1 (2026-07-11): in-flight dispatch wedge family.

Racing e2e evidence: active_dispatch_id was persisted before the charging
primitive raised (budget block) on the rework path, and a runtime restart
reset a mid-dispatch worker pane — both left the task claiming an in-flight
worker forever. Scheduler, silent-stall sweep (assigned-without-dispatched
shape only) and restart reconciliation all skipped it; the pipeline froze
until an operator re-assign.

Three fixes under test:
1. `_send_transport_task` rolls back in-flight bookkeeping on any pre-send
   failure (budget block / transport failure) — covers every caller.
2. `sweep_dead_dispatches` reports in-flight tasks whose assignee shows zero
   event activity (dispatched-but-dead shape).
3. `requeue_stale_inflight_tasks` (shared with graceful stop) runs at zf
   start boot so a non-graceful restart releases in-flight WIP.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from zf.runtime.dispatch_sweep import sweep_dead_dispatches
from zf.runtime.orchestrator import BudgetExceededError, Orchestrator
from zf.runtime.shutdown import requeue_stale_inflight_tasks
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import DispatchContext, TmuxTransport


NOW = datetime(2026, 7, 11, 6, 0, 0, tzinfo=timezone.utc)


def _ts(seconds_ago: float) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


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


def _orchestrator(state_dir: Path, transport) -> Orchestrator:
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )
    return Orchestrator(state_dir, cfg, transport)


def _seed_inflight(orch: Orchestrator, state_dir: Path) -> tuple[str, str]:
    task_id, dispatch_id = "T1", "disp-wedge"
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id=task_id, title="x", assigned_to="dev"))
    store.update(
        task_id,
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id=dispatch_id,
    )
    orch._remember_dispatch_id(task_id, dispatch_id)
    return task_id, dispatch_id


class TestChargingPrimitiveRollback:
    def test_budget_block_rolls_back_inflight_bookkeeping(
        self, state_dir, transport, monkeypatch
    ):
        orch = _orchestrator(state_dir, transport)
        task_id, dispatch_id = _seed_inflight(orch, state_dir)
        monkeypatch.setattr(orch, "_budget_exceeded", lambda role: True)
        context = DispatchContext(task_id=task_id, dispatch_id=dispatch_id)

        with pytest.raises(BudgetExceededError):
            orch._send_transport_task(
                "dev", state_dir / "briefing.md", "prompt", context
            )

        assert task_id not in orch._active_dispatch_ids
        assert (
            TaskStore(state_dir / "kanban.json").get(task_id).active_dispatch_id
            == ""
        )

    def test_transport_failure_rolls_back_inflight_bookkeeping(
        self, state_dir, transport, monkeypatch
    ):
        orch = _orchestrator(state_dir, transport)
        task_id, dispatch_id = _seed_inflight(orch, state_dir)

        def _boom(*args, **kwargs):
            raise RuntimeError("send failed")

        monkeypatch.setattr(orch.transport, "send_task", _boom)
        context = DispatchContext(task_id=task_id, dispatch_id=dispatch_id)

        with pytest.raises(RuntimeError):
            orch._send_transport_task(
                "dev", state_dir / "briefing.md", "prompt", context
            )

        assert task_id not in orch._active_dispatch_ids
        assert (
            TaskStore(state_dir / "kanban.json").get(task_id).active_dispatch_id
            == ""
        )

    def test_rollback_leaves_newer_dispatch_untouched(
        self, state_dir, transport, monkeypatch
    ):
        # A stale context (rotated dispatch_id) must not clobber the
        # bookkeeping of a newer dispatch.
        orch = _orchestrator(state_dir, transport)
        task_id, _ = _seed_inflight(orch, state_dir)
        orch._remember_dispatch_id(task_id, "disp-newer")
        TaskStore(state_dir / "kanban.json").update(
            task_id, active_dispatch_id="disp-newer"
        )
        monkeypatch.setattr(orch, "_budget_exceeded", lambda role: True)
        stale = DispatchContext(task_id=task_id, dispatch_id="disp-stale")

        with pytest.raises(BudgetExceededError):
            orch._send_transport_task(
                "dev", state_dir / "briefing.md", "prompt", stale
            )

        assert orch._active_dispatch_ids[task_id] == "disp-newer"
        assert (
            TaskStore(state_dir / "kanban.json").get(task_id).active_dispatch_id
            == "disp-newer"
        )


class TestDeadDispatchSweep:
    def _events(self, *specs) -> list[ZfEvent]:
        return [
            ZfEvent(
                type=spec.get("type", "agent.usage"),
                actor=spec.get("actor"),
                task_id=spec.get("task_id"),
                ts=spec["ts"],
            )
            for spec in specs
        ]

    def test_silent_inflight_beyond_threshold_reported(self):
        events = self._events(
            {"type": "loop.started", "actor": "zf-cli", "ts": _ts(600)},
            {"type": "task.dispatched", "task_id": "T1", "ts": _ts(400)},
        )
        result = sweep_dead_dispatches(
            inflight=[("T1", "review", "disp-1")], events=events, now=NOW
        )
        assert [d[:3] for d in result.dead_dispatches] == [
            ("T1", "review", "disp-1")
        ]
        assert result.dead_dispatches[0][3] == pytest.approx(400, abs=1)

    def test_recent_assignee_activity_suppresses(self):
        events = self._events(
            {"type": "task.dispatched", "task_id": "T1", "ts": _ts(400)},
            {"type": "agent.usage", "actor": "review", "ts": _ts(30)},
        )
        result = sweep_dead_dispatches(
            inflight=[("T1", "review", "disp-1")], events=events, now=NOW
        )
        assert result.dead_dispatches == []

    def test_role_prefix_actor_counts_as_life(self):
        # assigned_to may be role-level ("dev") while events come from the
        # bound replica ("dev-1").
        events = self._events(
            {"type": "task.dispatched", "task_id": "T1", "ts": _ts(400)},
            {"type": "worker.heartbeat", "actor": "dev-1", "ts": _ts(20)},
        )
        result = sweep_dead_dispatches(
            inflight=[("T1", "dev", "disp-1")], events=events, now=NOW
        )
        assert result.dead_dispatches == []

    def test_short_window_with_absent_pair_skipped(self):
        events = self._events(
            {"type": "loop.started", "actor": "zf-cli", "ts": _ts(60)},
        )
        result = sweep_dead_dispatches(
            inflight=[("T1", "review", "disp-1")], events=events, now=NOW
        )
        assert result.dead_dispatches == []


class TestBootInflightReconcile:
    def test_no_progress_inflight_requeued_with_source(self, tmp_path):
        sd = tmp_path / ".zf"
        sd.mkdir()
        log = EventLog(sd / "events.jsonl")
        store = TaskStore(sd / "kanban.json")
        store.add(Task(id="T1", title="x"))
        store.update(
            "T1",
            status="in_progress",
            assigned_to="review",
            active_dispatch_id="disp-dead",
        )
        log.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id="T1",
            payload={"dispatch_id": "disp-dead"},
        ))

        changed = requeue_stale_inflight_tasks(
            sd, log, source="zf_start_boot_reconcile", reason="boot"
        )

        assert changed is True
        task = store.get("T1")
        assert task.status == "backlog"
        assert task.active_dispatch_id == ""
        requeued = [e for e in log.read_all() if e.type == "task.requeued"]
        assert len(requeued) == 1
        assert requeued[0].payload["source"] == "zf_start_boot_reconcile"

    def test_progressed_dispatch_preserved_for_handoff_reconcile(self, tmp_path):
        sd = tmp_path / ".zf"
        sd.mkdir()
        log = EventLog(sd / "events.jsonl")
        store = TaskStore(sd / "kanban.json")
        store.add(Task(id="T1", title="x"))
        store.update(
            "T1",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="disp-live",
        )
        log.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id="T1",
            payload={"dispatch_id": "disp-live"},
        ))
        log.append(ZfEvent(
            type="dev.build.done",
            actor="dev-1",
            task_id="T1",
            payload={"dispatch_id": "disp-live"},
        ))

        changed = requeue_stale_inflight_tasks(
            sd, log, source="zf_start_boot_reconcile", reason="boot"
        )

        assert changed is False
        task = store.get("T1")
        assert task.status == "in_progress"
        assert task.active_dispatch_id == "disp-live"
        skipped = [e for e in log.read_all() if e.type == "task.requeue.skipped"]
        assert len(skipped) == 1

    def test_boot_reconcile_wired_into_start(self):
        # Wire-up discipline: the shared cleanup must actually run on the
        # start path, not only on graceful stop.
        source = Path("src/zf/cli/start.py").read_text(encoding="utf-8")
        assert "requeue_stale_inflight_tasks" in source
        assert "zf_start_boot_reconcile" in source


class TestWorkflowInvokeKernelOwned:
    """ZF-E2E-PRD-P1 (2026-07-11): workflow.invoke.requested is kernel-owned
    flow entry — in a Layer-2-active config it must fire the builtin primary
    (E1 bootstrap / reject) instead of falling to the 'Layer 2 owns it'
    branch and being silently dropped when the orchestrator role does not
    subscribe it (live: workflow-submit accepted, flow never started)."""

    def test_invoke_fires_builtin_primary_under_layer2(
        self, state_dir, transport
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="orchestrator",
                    backend="mock",
                    role_kind="reader",
                    stages=["meta"],
                    triggers=["dispatch.silent_stall"],  # invoke NOT subscribed
                ),
                RoleConfig(name="dev", backend="mock"),
            ],
        )
        orch = Orchestrator(state_dir, cfg, transport)
        event = ZfEvent(
            type="workflow.invoke.requested",
            actor="web",
            task_id="PRD-T1",
            payload={"task_id": "PRD-T1", "pattern_id": "nonexistent-stage"},
        )

        decisions = orch.run_once(events=[event])

        # The builtin primary ran: unknown pattern → deterministic reject,
        # not a silent no_action drop.
        rejected = [
            e for e in orch.event_log.read_all()
            if e.type == "workflow.invoke.rejected"
        ]
        assert rejected, "builtin E1 handler must fire under layer2_active"
        assert any(d.action == "block" for d in decisions)
