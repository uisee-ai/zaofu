"""Regression tests for B-REASSIGN-DISPATCH-01.

Bug: when N tasks all reassigned to the same single-replica role in
quick succession (e.g. 5 dev.build.done in a burst → orchestrator runs
`zf kanban assign TASK-X review` 5 times), the C3 WIP check used
``assigned_to`` to enumerate "active others". Every one of the
reassigned tasks saw the other N-1 as active peers on the same
instance and skipped itself to avoid WIP violation — none was ever
dispatched, review stayed idle forever.

Fix: the C3 WIP check now counts only tasks whose *latest
task.dispatched* assignee is this instance AND that are not currently
in the reassigned-pending-dispatch set. Reassigned-but-not-yet-
dispatched peers don't occupy the worker.

These tests pin the corrected behavior. Paired with
test_dispatch_assignee_change.py (the original C3 fixture).
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
def cfg():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(name="dev", backend="mock"),
            # single-replica review (the bug is about single-replica gridlock)
            RoleConfig(name="review", backend="mock"),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def _seed_reassigned_to_review(
    state_dir: Path, task_id: str, from_dev: str = "dev"
) -> None:
    """Record a task that was dispatched to `from_dev`, then reassigned
    (by Layer 2) to review. Status is in_progress, assigned_to=review."""
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id=task_id, title=task_id, status="in_progress",
                assigned_to="review"))
    log = EventLog(state_dir / "events.jsonl")
    # History: dispatch to dev, dev.build.done, reassign to review.
    log.append(ZfEvent(
        type="task.assigned", actor="zf-cli", task_id=task_id,
        payload={"assignee": from_dev, "role": "dev"},
    ))
    log.append(ZfEvent(
        type="task.dispatched", actor="orchestrator", task_id=task_id,
        payload={"assignee": from_dev, "role": "dev"},
    ))
    log.append(ZfEvent(
        type="dev.build.done", actor=from_dev, task_id=task_id,
        payload={},
    ))
    log.append(ZfEvent(
        type="task.assigned", actor="zf-cli", task_id=task_id,
        payload={"assignee": "review", "role": "review"},
    ))


class TestLatestDispatchedHelper:
    def test_empty_events_yields_empty_mapping(self, state_dir, cfg, transport):
        orch = Orchestrator(state_dir, cfg, transport)
        assert orch._latest_dispatched_per_task() == {}

    def test_later_assignment_clears_in_flight_dispatch(self, state_dir, cfg, transport):
        _seed_reassigned_to_review(state_dir, "TASK-A", from_dev="dev-1")
        _seed_reassigned_to_review(state_dir, "TASK-B", from_dev="dev-2")
        orch = Orchestrator(state_dir, cfg, transport)
        mapping = orch._latest_dispatched_per_task()
        assert "TASK-A" not in mapping
        assert "TASK-B" not in mapping

    def test_redispatch_overwrites_previous(self, state_dir, cfg, transport):
        _seed_reassigned_to_review(state_dir, "TASK-A", from_dev="dev-1")
        # Simulate a second dispatch to review (so latest becomes review).
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="task.dispatched", actor="orchestrator", task_id="TASK-A",
            payload={"assignee": "review", "role": "review"},
        ))
        orch = Orchestrator(state_dir, cfg, transport)
        assert orch._latest_dispatched_per_task()["TASK-A"] == "review"

    @pytest.mark.parametrize("terminal_type", [
        "fanout.child.completed",
        "fanout.child.failed",
    ])
    def test_fanout_terminal_clears_active_dispatch(
        self, state_dir, cfg, transport, terminal_type,
    ):
        ts = TaskStore(state_dir / "kanban.json")
        ts.add(Task(id="TASK-A", title="TASK-A", status="in_progress", assigned_to="dev-1"))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="fanout.child.dispatched",
            actor="zf-cli",
            task_id="TASK-A",
            payload={
                "fanout_id": "fanout-dev",
                "child_id": "TASK-A",
                "run_id": "run-1",
                "role_instance": "dev-1",
                "task_id": "TASK-A",
            },
        ))
        log.append(ZfEvent(
            type=terminal_type,
            actor="zf-cli",
            task_id="TASK-A",
            payload={
                "fanout_id": "fanout-dev",
                "child_id": "TASK-A",
                "run_id": "run-1",
                "role_instance": "dev-1",
                "task_id": "TASK-A",
                "status": "completed"
                if terminal_type.endswith(".completed")
                else "failed",
            },
        ))

        orch = Orchestrator(state_dir, cfg, transport)

        assert "TASK-A" not in orch._latest_dispatched_per_task()

    def test_fanout_terminal_clears_reassigned_pending_dispatch(
        self, state_dir, cfg, transport,
    ):
        ts = TaskStore(state_dir / "kanban.json")
        ts.add(Task(id="TASK-A", title="TASK-A", status="in_progress", assigned_to="dev-1"))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id="TASK-A",
            payload={"assignee": "dev-1", "role": "dev"},
        ))
        log.append(ZfEvent(
            type="fanout.child.dispatched",
            actor="zf-cli",
            task_id="TASK-A",
            payload={
                "fanout_id": "fanout-dev",
                "child_id": "TASK-A",
                "run_id": "run-1",
                "role_instance": "dev-1",
                "task_id": "TASK-A",
            },
        ))
        log.append(ZfEvent(
            type="fanout.child.completed",
            actor="zf-cli",
            task_id="TASK-A",
            payload={
                "fanout_id": "fanout-dev",
                "child_id": "TASK-A",
                "run_id": "run-1",
                "role_instance": "dev-1",
                "task_id": "TASK-A",
                "status": "completed",
            },
        ))

        orch = Orchestrator(state_dir, cfg, transport)

        assert orch._reassigned_pending_dispatch() == set()


class TestReassignGridlockResolved:
    """Before the fix: 2 reassigned tasks gridlocked and neither
    dispatched. After the fix: first cycle dispatches one, WIP=1
    correctly defers the other."""

    def test_two_reassigned_to_single_replica_review_first_cycle_dispatches_one(
        self, state_dir, cfg, transport,
    ):
        _seed_reassigned_to_review(state_dir, "TASK-A", from_dev="dev-1")
        _seed_reassigned_to_review(state_dir, "TASK-B", from_dev="dev-2")
        orch = Orchestrator(state_dir, cfg, transport)
        decisions = orch._dispatch_ready()
        dispatches = [d for d in decisions if d.action == "dispatch"]
        # At least one of the 2 reassigned tasks must dispatch —
        # pre-fix this returned 0 (gridlock).
        assert len(dispatches) == 1
        assert dispatches[0].task_id in ("TASK-A", "TASK-B")
        assert dispatches[0].role == "review"

    def test_reassigned_in_progress_dispatch_does_not_emit_invalid_transition(
        self, state_dir, cfg, transport,
    ):
        _seed_reassigned_to_review(state_dir, "TASK-A", from_dev="dev-1")
        orch = Orchestrator(state_dir, cfg, transport)

        decisions = orch._dispatch_ready()

        assert [d.task_id for d in decisions if d.action == "dispatch"] == [
            "TASK-A",
        ]
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not any(e.type == "task.invalid_transition" for e in events)
        task = TaskStore(state_dir / "kanban.json").get("TASK-A")
        assert task is not None
        assert task.status == "in_progress"
        assert task.assigned_to == "review"

    def test_second_cycle_defers_other_until_review_frees(
        self, state_dir, cfg, transport,
    ):
        _seed_reassigned_to_review(state_dir, "TASK-A", from_dev="dev-1")
        _seed_reassigned_to_review(state_dir, "TASK-B", from_dev="dev-2")
        orch = Orchestrator(state_dir, cfg, transport)
        first = orch._dispatch_ready()
        # After first cycle, one task has latest_dispatched=review and
        # is genuinely in flight. The *other* one is still reassigned-
        # pending-dispatch but review WIP=1 holds it back.
        second = orch._dispatch_ready()
        dispatched_in_second = [d for d in second if d.action == "dispatch"]
        assert dispatched_in_second == []

    def test_after_review_moves_on_other_task_dispatches(
        self, state_dir, cfg, transport,
    ):
        """Once the first task leaves review (reassigned to test / judge),
        the second one finally gets its chance."""
        _seed_reassigned_to_review(state_dir, "TASK-A", from_dev="dev-1")
        _seed_reassigned_to_review(state_dir, "TASK-B", from_dev="dev-2")
        orch = Orchestrator(state_dir, cfg, transport)
        decisions_1 = orch._dispatch_ready()
        winner = [d for d in decisions_1 if d.action == "dispatch"][0]
        # Advance winner: review finishes it, Layer 2 reassigns to test.
        # (In real pipeline this fires after review.approved + next
        # orchestrator wake. Here we simulate by writing the events.)
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="review.approved", actor="review", task_id=winner.task_id,
            payload={},
        ))
        # Layer 2 would now move winner to test; simulate:
        TaskStore(state_dir / "kanban.json").update(
            winner.task_id, assigned_to="test",
        )
        log.append(ZfEvent(
            type="task.assigned", actor="zf-cli", task_id=winner.task_id,
            payload={"assignee": "test", "role": "test"},
        ))
        # Now re-run dispatcher: review is free, the other task should go.
        orch2 = Orchestrator(state_dir, cfg, transport)
        decisions_2 = orch2._dispatch_ready()
        dispatched = [d for d in decisions_2 if d.action == "dispatch"]
        # At minimum: the loser (whichever wasn't picked first) now goes
        # to review. (It's possible the winner re-dispatches to test too
        # — we don't rule that out, we just require the review dispatch.)
        review_dispatches = [d for d in dispatched if d.role == "review"]
        assert len(review_dispatches) == 1
        assert review_dispatches[0].task_id != winner.task_id


class TestFiveTaskBurstDoesNotGridlock:
    """The full bug scenario: 5 tasks in burst. Before the fix this
    produced 0 dispatches forever; after the fix, running dispatch_ready
    5 times (interleaved with mock 'review.approved + reassign') clears
    all of them."""

    def test_five_reassigned_tasks_all_eventually_dispatch(
        self, state_dir, cfg, transport,
    ):
        task_ids = [f"TASK-{i}" for i in range(5)]
        for i, tid in enumerate(task_ids):
            _seed_reassigned_to_review(
                state_dir, tid,
                from_dev=f"dev-{(i % 2) + 1}",  # alternating dev-1/dev-2
            )

        dispatched_ids: list[str] = []
        for _ in range(5):
            orch = Orchestrator(state_dir, cfg, transport)
            decisions = orch._dispatch_ready()
            new = [d for d in decisions if d.action == "dispatch"
                   and d.role == "review"]
            if not new:
                # should not happen — would indicate gridlock
                break
            picked = new[0]
            dispatched_ids.append(picked.task_id)
            # Simulate review finishing this task and Layer 2 moving it
            # to test so review WIP frees up for the next iteration.
            log = EventLog(state_dir / "events.jsonl")
            log.append(ZfEvent(
                type="review.approved", actor="review",
                task_id=picked.task_id, payload={},
            ))
            TaskStore(state_dir / "kanban.json").update(
                picked.task_id, assigned_to="test",
            )
            log.append(ZfEvent(
                type="task.assigned", actor="zf-cli",
                task_id=picked.task_id,
                payload={"assignee": "test", "role": "test"},
            ))

        assert len(dispatched_ids) == 5
        assert set(dispatched_ids) == set(task_ids)


class TestReworkDispatchDedup:
    def test_rework_records_assignment_so_dispatch_ready_does_not_loop(
        self, state_dir, cfg, transport,
    ):
        task = Task(
            id="TASK-R",
            title="rework target",
            status="in_progress",
            assigned_to="judge",
            retry_count=1,
        )
        TaskStore(state_dir / "kanban.json").add(task)
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id=task.id,
            payload={"role": "judge", "assignee": "judge"},
        ))
        log.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id=task.id,
            payload={"role": "judge", "assignee": "judge"},
        ))
        trigger = ZfEvent(
            type="discriminator.failed",
            actor="zf-cli",
            task_id=task.id,
            payload={"failed_d": ["ArchitectureRulesD"]},
        )
        log.append(trigger)

        orch = Orchestrator(state_dir, cfg, transport)
        reloaded = TaskStore(state_dir / "kanban.json").get(task.id)
        assert reloaded is not None

        assert orch._dispatch_rework(reloaded, trigger) == "dev"

        events = EventLog(state_dir / "events.jsonl").read_all()
        rework_assignments = [
            event for event in events
            if event.type == "task.assigned"
            and event.task_id == task.id
            and isinstance(event.payload, dict)
            and event.payload.get("source") == "rework"
        ]
        assert len(rework_assignments) == 1
        assert rework_assignments[0].payload["assignee"] == "dev"
        assert orch._reassigned_pending_dispatch() == set()

        before = [
            event for event in events
            if event.type == "task.dispatched" and event.task_id == task.id
        ]
        assert orch._dispatch_ready() == []
        after = [
            event for event in EventLog(state_dir / "events.jsonl").read_all()
            if event.type == "task.dispatched" and event.task_id == task.id
        ]
        assert len(after) == len(before)
