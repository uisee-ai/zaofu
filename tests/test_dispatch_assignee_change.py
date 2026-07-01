"""Fix C3 — Layer 1 dispatches when Layer 2 reassigns a task.

Run 13 finding: after dev-1 emits dev.build.done Layer 2 wakes and
reassigns the same TASK to review (status stays in_progress because
the briefing forbids manual move). Without C3 the task sat in
in_progress assigned to review with no task.dispatched ever firing.

C3 rule: if there exists a task.assigned event with a different assignee X
newer than the most recent task.dispatched event for that task (or there
is no dispatch event at all), Layer 1 sends the briefing to X. Same-assignee
assignments newer than dispatch are treated as state-sync echoes unless the
payload explicitly asks for redispatch. Dedup key is the assignee field —
re-running run_once won't double-dispatch.

Tests use the events.jsonl rather than direct kanban writes so the
dedup logic actually has events to read (test fixtures that bypass
events are correctly ignored — see TestNoEventsNoSpuriousDispatch).
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
    EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def layer2_config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(name="dev", backend="mock"),
            RoleConfig(name="review", backend="mock"),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def _seed(state_dir: Path, task: Task, *, assigned_history: list[tuple[str, str]],
          dispatched_history: list[tuple[str, str]]) -> EventLog:
    """Write the given task to kanban + replay event history.
    assigned_history / dispatched_history are lists of plain assignee
    strings (the test was written using tuples for grouping; flatten)."""
    TaskStore(state_dir / "kanban.json").add(task)
    log = EventLog(state_dir / "events.jsonl")
    for entry in assigned_history:
        a = entry[0] if isinstance(entry, tuple) else entry
        log.append(ZfEvent(
            type="task.assigned", actor="zf-cli", task_id=task.id,
            payload={"assignee": a, "role": a},
        ))
    for entry in dispatched_history:
        a = entry[0] if isinstance(entry, tuple) else entry
        log.append(ZfEvent(
            type="task.dispatched", actor="orchestrator", task_id=task.id,
            payload={"assignee": a, "role": a},
        ))
    return log


class TestC3Reassignment:
    def test_reassigned_task_dispatches_to_new_assignee(
        self, state_dir, layer2_config, transport,
    ):
        # dev-1 was originally assigned + dispatched, then Layer 2
        # reassigns to review. Layer 1 should pick the review dispatch.
        task = Task(title="T1", status="in_progress", assigned_to="review")
        TaskStore(state_dir / "kanban.json").add(task)
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id=task.id,
            payload={"assignee": "dev", "role": "dev"},
        ))
        log.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id=task.id,
            payload={"assignee": "dev", "role": "dev"},
        ))
        log.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id=task.id,
            payload={"assignee": "review", "role": "review"},
        ))

        orch = Orchestrator(state_dir, layer2_config, transport)
        decisions = orch._dispatch_ready()
        assert any(d.action == "dispatch" and d.task_id == task.id
                   for d in decisions)

        # task.dispatched now records review as the assignee.
        events = EventLog(state_dir / "events.jsonl").read_all()
        last_dispatched = [
            e for e in events if e.type == "task.dispatched"
            and e.task_id == task.id
        ][-1]
        assert last_dispatched.payload.get("assignee") == "review"

    def test_no_redispatch_when_already_at_assignee(
        self, state_dir, layer2_config, transport,
    ):
        # dev was assigned + dispatched. No reassignment yet.
        # Layer 1 cycle should NOT re-dispatch (dedup works).
        task = Task(title="T1", status="in_progress", assigned_to="dev")
        _seed(state_dir, task,
              assigned_history=[("dev",)],
              dispatched_history=[("dev",)])

        orch = Orchestrator(state_dir, layer2_config, transport)
        before = len(EventLog(state_dir / "events.jsonl").read_all())
        orch._dispatch_ready()
        after = len(EventLog(state_dir / "events.jsonl").read_all())
        # No new task.dispatched event
        assert after == before

    def test_same_assignee_assignment_after_dispatch_does_not_loop(
        self, state_dir, layer2_config, transport,
    ):
        # Layer 2 can echo the same assignment after Layer 1 already
        # dispatched it. That is state sync, not a new dispatch request.
        task = Task(title="T1", status="in_progress", assigned_to="review")
        TaskStore(state_dir / "kanban.json").add(task)
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id=task.id,
            payload={"assignee": "review", "role": "review"},
        ))
        log.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id=task.id,
            payload={"assignee": "review", "role": "review"},
        ))
        log.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id=task.id,
            payload={"assignee": "review", "role": "review"},
        ))

        orch = Orchestrator(state_dir, layer2_config, transport)
        decisions = orch._dispatch_ready()

        assert not any(d.action == "dispatch" and d.task_id == task.id
                       for d in decisions)

    def test_same_assignee_explicit_reissue_after_dispatch_redispatches(
        self, state_dir, layer2_config, transport,
    ):
        # Terminal evidence repair can ask the same role to re-issue a
        # payload after its prior dispatch finished. Same-assignee
        # redelivery must be explicit so ordinary Layer 2 echoes do not loop.
        task = Task(title="T1", status="in_progress", assigned_to="review")
        TaskStore(state_dir / "kanban.json").add(task)
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id=task.id,
            payload={"assignee": "review", "role": "review"},
        ))
        log.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id=task.id,
            payload={"assignee": "review", "role": "review"},
        ))
        log.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "assignee": "review",
                "role": "review",
                "source": "terminal_evidence_repair",
            },
        ))

        orch = Orchestrator(state_dir, layer2_config, transport)
        decisions = orch._dispatch_ready()

        assert any(d.action == "dispatch" and d.task_id == task.id
                   for d in decisions)

    def test_no_dispatch_for_orchestrator_role(
        self, state_dir, layer2_config, transport,
    ):
        # Defensive: never dispatch a task that's been reassigned to
        # the orchestrator role (Layer 2 itself).
        task = Task(title="T1", status="in_progress",
                    assigned_to="orchestrator")
        _seed(state_dir, task,
              assigned_history=[("orchestrator",)],
              dispatched_history=[])
        orch = Orchestrator(state_dir, layer2_config, transport)
        decisions = orch._dispatch_ready()
        assert not any(d.action == "dispatch" for d in decisions)


class TestNoEventsNoSpuriousDispatch:
    def test_in_progress_task_with_no_events_is_left_alone(
        self, state_dir, layer2_config, transport,
    ):
        # Test fixtures often write kanban directly without events. C3
        # must not pick those up — empty event log means empty
        # reassigned set means no candidates beyond backlog.
        TaskStore(state_dir / "kanban.json").add(
            Task(title="T1", status="in_progress", assigned_to="dev")
        )
        orch = Orchestrator(state_dir, layer2_config, transport)
        decisions = orch._dispatch_ready()
        # Only dispatches should be from backlog, and there is no
        # backlog task. So no dispatch.
        assert not any(d.action == "dispatch" for d in decisions)
