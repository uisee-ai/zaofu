"""Tests for orchestrator core — deterministic dispatch loop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from zf.core.config.schema import ZfConfig, ProjectConfig, RoleConfig, SessionConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore, SessionState
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator, OrchestratorDecision
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def state_dir(tmp_path: Path):
    """Set up a complete .zf/ state directory."""
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    (sd / "logs").mkdir()

    # Event log with session.started
    event_log = EventLog(sd / "events.jsonl")
    event_log.append(ZfEvent(type="session.started", actor="zf-cli"))
    event_log.append(ZfEvent(type="loop.started", actor="zf-cli"))

    # Session state
    session_store = SessionStore(sd / "session.yaml")
    session_store.create(project_root=str(tmp_path))
    session_store.update(runtime_state="active")

    # Empty kanban
    (sd / "kanban.json").write_text("[]\n")

    return sd


@pytest.fixture
def config():
    return ZfConfig(
        project=ProjectConfig(name="test"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[
            RoleConfig(
                name="dev", backend="mock", stages=["implement"],
                publishes=["dev.build.done", "dev.blocked"],
            ),
            RoleConfig(
                name="review", backend="mock", stages=["code_review"],
                publishes=["review.approved", "review.rejected"],
            ),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True))


class TestBlockedHumanAutoClearOnStart:
    """B-NEW-6 regression: prior orchestrator runs may have parked workers
    in ``blocked_human`` state when the respawn-failure cooldown tripped.
    That state persists in events.jsonl so a restart silently inherits
    the block — _worker_dispatchable() returns False, dispatch rejects
    every task, and no observability event fires (the gate is BEFORE the
    dispatch_skipped emit).

    Fix: Orchestrator.__init__ clears blocked_human after rebuilding state
    from event history. Treating ``zf start`` as the operator escalation
    that resolved the cooldown.
    """

    def test_blocked_human_is_cleared_on_init(
        self, state_dir, config, transport,
    ):
        # Pre-populate event log with a worker parked in blocked_human
        # (matches the cangjie r-next-5 cascade pattern: refresh storm →
        # respawn failures → 3 fails in 120s → blocked_human).
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="worker.state.changed",
            actor="dev",
            payload={"from": "respawning", "to": "blocked_human",
                     "reason": "respawn cap exhausted"},
        ))

        orch = Orchestrator(state_dir, config, transport)

        # _last_worker_state must now show dev as idle
        assert orch._last_worker_state.get("dev") == "idle", (
            f"expected dev cleared to idle; got "
            f"{orch._last_worker_state.get('dev')!r}"
        )

        # New worker.state.changed event must be emitted so persistence
        # is consistent and future restarts won't replay the block.
        events = list(log.read_all())
        clears = [
            e for e in events
            if e.type == "worker.state.changed"
            and e.actor == "dev"
            and e.payload.get("from") == "blocked_human"
            and e.payload.get("to") == "idle"
        ]
        assert clears, (
            f"expected a clear event from blocked_human → idle; got "
            f"{[(e.type, e.payload) for e in events]}"
        )

    def test_dispatch_works_after_clear(
        self, state_dir, config, transport,
    ):
        """End-to-end: parked → init clears → dispatch fires normally.

        Without the fix this is the exact silent-stall scenario from
        cangjie r-next-5: task in backlog with assigned_to=dev, kernel
        emits no task.dispatched event, pane sits at 0 tokens forever.
        """
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="worker.state.changed",
            actor="dev",
            payload={"from": "respawning", "to": "blocked_human"},
        ))
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            title="Stuck task", id="T-blocked-test",
            status="backlog", assigned_to="dev",
        ))

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) == 1, (
            f"expected dispatch after blocked_human clear; got "
            f"actions {[d.action for d in decisions]}"
        )
        assert dispatch[0].task_id == "T-blocked-test"
        assert dispatch[0].role == "dev"

    def test_idempotent_when_no_blocked_workers(
        self, state_dir, config, transport,
    ):
        """No-op when no workers are blocked — must not emit phantom
        state.changed events for already-idle instances."""
        log = EventLog(state_dir / "events.jsonl")
        events_before = len(list(log.read_all()))

        Orchestrator(state_dir, config, transport)

        # Only initial session.started + loop.started + any infra events
        # from fixture should be present — no new state.changed clears.
        events_after = list(log.read_all())
        new_state_changed = [
            e for e in events_after[events_before:]
            if e.type == "worker.state.changed"
        ]
        assert not new_state_changed, (
            f"expected no state clears when nothing blocked; got "
            f"{[(e.actor, e.payload) for e in new_state_changed]}"
        )

    def test_only_blocked_human_cleared_other_states_preserved(
        self, state_dir, config, transport,
    ):
        """The clear must be narrow: only blocked_human → idle.
        Other non-dispatchable states (stuck, draining, retired)
        have their own recovery paths and must not be reset."""
        log = EventLog(state_dir / "events.jsonl")
        for actor, to_state in [
            ("dev", "blocked_human"),
            ("review", "draining"),
            ("test", "retired"),
        ]:
            log.append(ZfEvent(
                type="worker.state.changed",
                actor=actor,
                payload={"from": "idle", "to": to_state},
            ))

        orch = Orchestrator(state_dir, config, transport)

        assert orch._last_worker_state.get("dev") == "idle"
        assert orch._last_worker_state.get("review") == "draining"
        assert orch._last_worker_state.get("test") == "retired"


class TestOrchestratorDispatch:
    def test_dispatches_ready_task_to_idle_worker(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build auth", id="T1", status="backlog"))

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()

        assert len(decisions) >= 1
        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) == 1
        assert dispatch[0].task_id == "T1"

    def test_candidate_terminal_success_quiesces_old_feature_dispatch(
        self,
        state_dir,
        config,
        transport,
    ):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="judge.passed",
            actor="judge",
            payload={"pdd_id": "F-1", "feature_id": "F-1"},
        ))
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            title="Old feature task",
            id="T1",
            key="F-1:T1",
            status="backlog",
            assigned_to="dev",
        ))

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()

        events = log.read_all()
        assert not [d for d in decisions if d.action == "dispatch"]
        assert not [
            event for event in events
            if event.type in {"task.dispatched", "dispatch.blocked"}
            and event.task_id == "T1"
        ]

    def test_does_not_dispatch_when_no_tasks(self, state_dir, config, transport):
        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()
        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) == 0

    def test_does_not_dispatch_blocked_task(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="A", id="T1", status="backlog"))
        store.add(Task(title="B", id="T2", status="backlog", blocked_by=["T1"]))

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()
        dispatch = [d for d in decisions if d.action == "dispatch"]
        # Only T1 should be dispatched
        assert len(dispatch) == 1
        assert dispatch[0].task_id == "T1"

    def test_assigned_backlog_task_still_respects_blocked_by(
        self, state_dir, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="A", id="T1", status="in_progress"))
        store.add(Task(
            title="B",
            id="T2",
            status="backlog",
            assigned_to="dev",
            blocked_by=["T1"],
        ))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id="T2",
            payload={"role": "dev", "assignee": "dev"},
        ))

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert dispatch == []
        assert store.get("T2").status == "backlog"

    def test_done_unblocks_dependency_blocked_task(
        self, state_dir, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="A", id="T1", status="in_progress"))
        store.add(Task(
            title="B",
            id="T2",
            status="blocked",
            blocked_by=["T1"],
        ))

        orch = Orchestrator(state_dir, config, transport)

        assert orch._move_task("T1", "done", trigger_event="judge.passed")

        unblocked = store.get("T2")
        assert unblocked is not None
        assert unblocked.status == "backlog"
        assert unblocked.blocked_by == ["T1"]

        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(
            event.type == "task.status_changed"
            and event.task_id == "T2"
            and event.payload.get("from") == "blocked"
            and event.payload.get("to") == "backlog"
            and event.payload.get("source") == "dependency_resolved"
            for event in events
        )

    def test_done_does_not_unblock_human_blocked_task(
        self, state_dir, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="A", id="T1", status="in_progress"))
        store.add(Task(
            title="B",
            id="T2",
            status="blocked",
            blocked_by=["T1"],
            blocked_reason="waiting for operator input",
        ))

        orch = Orchestrator(state_dir, config, transport)

        assert orch._move_task("T1", "done", trigger_event="judge.passed")

        still_blocked = store.get("T2")
        assert still_blocked is not None
        assert still_blocked.status == "blocked"
        assert still_blocked.blocked_reason == "waiting for operator input"

    def test_dispatch_assigns_to_role(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="backlog"))

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()
        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert dispatch[0].role is not None

    def test_dispatch_moves_to_in_progress(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="backlog"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        # Check task was moved
        task = store.get("T1")
        assert task.status == "in_progress"

    def test_dispatch_emits_event(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="backlog"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        event_log = EventLog(state_dir / "events.jsonl")
        events = event_log.read_all()
        types = [e.type for e in events]
        assert "task.dispatched" in types


class TestOrchestratorWIP:
    def test_respects_wip_limit(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Active", id="T1", status="in_progress", assigned_to="dev"))
        store.add(Task(title="Waiting", id="T2", status="backlog"))

        # Only 1 dev role, already busy
        config_single = ZfConfig(
            project=ProjectConfig(name="test"),
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        orch = Orchestrator(state_dir, config_single, transport)
        decisions = orch.run_once()
        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) == 0


class TestOrchestratorEventReaction:
    def test_build_done_triggers_review_move(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="in_progress", assigned_to="dev"))

        # Simulate dev.build.done event
        event_log = EventLog(state_dir / "events.jsonl")
        event_log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()

        # Task should be moved to review
        task = store.get("T1")
        assert task.status == "review"


class TestOrchestratorBriefing:
    def test_dispatch_writes_briefing_file(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build auth", id="T1", status="backlog"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        briefing_dir = state_dir / "briefings"
        assert briefing_dir.exists()
        briefing_files = list(briefing_dir.glob("dev-T1*"))
        assert len(briefing_files) >= 1
        content = briefing_files[0].read_text()
        assert "T1" in content
        assert "Build auth" in content

    def test_dispatch_writes_task_json(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build auth", id="T1", status="backlog"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        task_json = state_dir / "briefings" / "T1.json"
        assert task_json.exists()
        data = json.loads(task_json.read_text())
        assert data["id"] == "T1"
        assert data["assigned_to"] == "dev"

    def test_dispatch_writes_instructions(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="backlog"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        instructions = state_dir / "instructions" / "dev.md"
        assert instructions.exists()
        content = instructions.read_text()
        assert "dev" in content
        assert "zf emit" in content

    def test_dispatch_blocks_when_strict_run_contract_not_hydrated(
        self,
        state_dir,
        config,
        transport,
        monkeypatch,
    ):
        (state_dir / "config").mkdir()
        (state_dir / "config" / "run-contract.json").write_text(
            json.dumps({
                "schema_version": "run-contract.v1",
                "contract_digest": "digest-strict",
                "workflow": {"strictness": "full-parity"},
                "refs": {"task_map": ["docs/task-map.json"]},
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "zf.runtime.orchestrator_dispatch.generate_role_instructions",
            lambda *args, **kwargs: "# dev\n\nmissing run contract context\n",
        )
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="backlog"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        task = store.get("T1")
        assert task.status == "backlog"
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert "dispatch.briefing_hydration.failed" in [event.type for event in events]
        assert "task.dispatched" not in [event.type for event in events]

    def test_dispatch_writes_skills_lockfile(self, state_dir, config, transport):
        (state_dir / "skills" / "reviewer").mkdir(parents=True)
        (state_dir / "skills" / "reviewer" / "SKILL.md").write_text(
            "---\n"
            "name: reviewer\n"
            "description: Reviews code. Use when checking changes.\n"
            "---\n\n"
            "# Reviewer\n",
            encoding="utf-8",
        )
        config.roles[0].skills = ["reviewer"]
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="backlog"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        lock = json.loads((state_dir / "skills.lock.json").read_text())
        assert lock["skills"][0]["name"] == "reviewer"
        assert lock["skills"][0]["task_id"] == "T1"
        assert lock["skills"][0]["status"] == "resolved"

    def test_dispatch_sends_to_tmux(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="backlog"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        # In dry-run, check command_log has send-keys
        assert any("send-keys" in cmd for cmd in transport.tmux.command_log)

    def test_briefing_includes_completion_command(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="backlog"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        briefing = list((state_dir / "briefings").glob("dev-T1*"))[0].read_text()
        assert "zf emit" in briefing
        assert "dev.build.done" in briefing


class TestOrchestratorRework:
    def test_review_rejected_dispatches_rework(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="review", assigned_to="dev"))

        event_log = EventLog(state_dir / "events.jsonl")
        event_log.append(ZfEvent(
            type="review.rejected", actor="review", task_id="T1",
            payload={"reason": "Missing error handling"},
        ))

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) >= 1
        assert dispatch[0].task_id == "T1"
        assert "rework" in dispatch[0].reason

    def test_layer2_review_rejected_from_in_progress_dispatches_rework(
        self, state_dir, config, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            title="Build",
            id="T1",
            status="in_progress",
            assigned_to="review",
        ))

        event_log = EventLog(state_dir / "events.jsonl")
        event_log.append(ZfEvent(
            type="review.rejected", actor="review", task_id="T1",
            payload={"reason": "Missing empty and unicode tests"},
        ))

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) >= 1
        assert dispatch[0].task_id == "T1"
        events = event_log.read_all()
        assert any(e.type == "task.rework.requested" for e in events)
        briefing = state_dir / "briefings" / "dev-T1-rework.md"
        assert "Missing empty and unicode tests" in briefing.read_text()

    def test_test_failed_dispatches_rework(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="testing", assigned_to="dev"))

        event_log = EventLog(state_dir / "events.jsonl")
        event_log.append(ZfEvent(type="test.failed", actor="test", task_id="T1"))

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) >= 1

    def test_layer2_test_failed_from_in_progress_dispatches_rework(
        self, state_dir, config, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="in_progress", assigned_to="test"))

        event_log = EventLog(state_dir / "events.jsonl")
        event_log.append(ZfEvent(type="test.failed", actor="test", task_id="T1"))

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) >= 1
        assert any(e.type == "task.rework.requested" for e in event_log.read_all())

    def test_verify_failed_from_in_progress_dispatches_rework(
        self, state_dir, config, transport,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="in_progress", assigned_to="verify"))

        event_log = EventLog(state_dir / "events.jsonl")
        event_log.append(ZfEvent(type="verify.failed", actor="verify", task_id="T1"))

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()

        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) >= 1
        assert any(e.type == "task.rework.requested" for e in event_log.read_all())


class TestOrchestratorLogCapture:
    def test_captures_pane_logs(self, state_dir, config, transport):
        store = TaskStore(state_dir / "kanban.json")
        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        logs_dir = state_dir / "logs"
        assert logs_dir.exists()


class TestOrchestratorEventOffset:
    def test_run_once_accepts_explicit_events_list(self, state_dir, config, transport):
        """When events are pushed in by the watcher, run_once should react
        to them without re-querying the EventLog."""
        from zf.core.task.store import TaskStore
        from zf.core.events.model import ZfEvent
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="in_progress", assigned_to="dev"))
        orch = Orchestrator(state_dir, config, transport)
        # Push an event directly without writing to events.jsonl
        pushed = ZfEvent(type="dev.build.done", actor="dev", task_id="T1")
        orch.run_once(events=[pushed])
        task = store.get("T1")
        assert task.status == "review"

    def test_run_once_persists_event_offset_to_session(self, state_dir, config, transport):
        from zf.core.events.log import EventLog
        from zf.core.events.model import ZfEvent
        from zf.core.state.session import SessionStore
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))
        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()
        session = SessionStore(state_dir / "session.yaml").load()
        assert session.latest_event_offset > 0

    def test_restart_does_not_reprocess_events_before_offset(
        self, state_dir, config, transport
    ):
        """If the orchestrator persisted an offset and restarts, events at
        positions before that offset must NOT be reacted to again."""
        from zf.core.events.log import EventLog
        from zf.core.events.model import ZfEvent
        from zf.core.state.session import SessionStore
        from zf.core.task.store import TaskStore
        log = EventLog(state_dir / "events.jsonl")
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(title="Build", id="T1", status="in_progress", assigned_to="dev"))
        log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))

        # First run: process the event, move T1 to review, persist offset
        Orchestrator(state_dir, config, transport).run_once()
        assert store.get("T1").status == "review"
        # Manually move it back to in_progress and restart — if offset is honored,
        # the second run_once should NOT see the old dev.build.done again.
        store.update("T1", status="in_progress")
        Orchestrator(state_dir, config, transport).run_once()
        assert store.get("T1").status == "in_progress", \
            "orchestrator re-reacted to events before the persisted offset"


class TestOrchestratorDecisionDataclass:
    def test_decision_fields(self):
        d = OrchestratorDecision(action="dispatch", task_id="T1", role="dev", reason="ready")
        assert d.action == "dispatch"
        assert d.task_id == "T1"
        assert d.role == "dev"
