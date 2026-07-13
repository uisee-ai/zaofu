"""P0-2 (2026-07-09): workflow_resume_apply must be event-first.

The terminal / dispatch events belong in events.jsonl (the single source of
truth, I1) *before* the store projection is applied — the projection, for a
terminal status, archives + pops the task. The old order projected first, so a
crash in the window between the two left events.jsonl missing the terminal event
while the archive already showed the task done; build() iterates active tasks
only, so the terminal event was never re-emitted → lost forever, and any
blocked_by dependents deadlocked.

These tests inject a crash at the projection step and assert the events were
already durable. Under the old projection-first order the store mutation ran
first and raised before any append, so these would fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.workflow_resume import WorkflowResumeCheckpoint
from zf.runtime.workflow_resume_apply import _apply_checkpoint


def _setup(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    store = TaskStore(state_dir / "kanban.json")
    return log, writer, store


def _terminal_checkpoint() -> WorkflowResumeCheckpoint:
    return WorkflowResumeCheckpoint(
        task_id="T-DONE",
        last_trusted_event_id="",
        last_completed_stage="review",
        expected_next_stage="",
        expected_next_role="",
        blocking_event_id="",
        safe_resume_action="needs_terminal_closeout",
        idempotency_key="wfres-term-test",
    )


def _dispatch_checkpoint() -> WorkflowResumeCheckpoint:
    return WorkflowResumeCheckpoint(
        task_id="T-DISP",
        last_trusted_event_id="",
        last_completed_stage="arch",
        expected_next_stage="dev",
        expected_next_role="dev",
        blocking_event_id="",
        safe_resume_action="needs_stage_dispatch",
        idempotency_key="wfres-disp-test",
    )


def test_terminal_closeout_normal_path(tmp_path: Path) -> None:
    log, writer, store = _setup(tmp_path)
    store.add(Task(id="T-DONE", title="x", status="in_progress"))

    result = _apply_checkpoint(
        store, writer, _terminal_checkpoint(), events=[], gate_dispatcher=None
    )

    assert result.applied is True
    types = [e.type for e in log.read_all()]
    assert "task.done.evidence" in types
    assert "task.status_changed" in types
    # Projection landed too (get reads through to the archive after pop).
    assert store.get("T-DONE").status == "done"


def test_terminal_event_survives_projection_crash(tmp_path: Path) -> None:
    """The P0-2 headline: crash during the (archiving) projection must not lose
    the terminal event — it is appended first."""
    log, writer, store = _setup(tmp_path)
    store.add(Task(id="T-DONE", title="x", status="in_progress"))

    def boom(*args, **kwargs):
        raise RuntimeError("crash during store projection")

    store.update = boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        _apply_checkpoint(
            store, writer, _terminal_checkpoint(), events=[], gate_dispatcher=None
        )

    events = log.read_all()
    types = [e.type for e in events]
    # Event-first: both terminal events are already durable despite the crash.
    assert "task.done.evidence" in types
    assert any(
        e.type == "task.status_changed" and e.payload.get("to") == "done"
        for e in events
    )


def test_dispatch_events_survive_assignment_projection_crash(tmp_path: Path) -> None:
    """The dispatch variant: assign/dispatch events must precede the assigned_to
    projection, else a crash leaves assigned_to set with no assign event and
    already-done reads it as 'already dispatched' → task stuck assigned-not-sent."""
    log, writer, store = _setup(tmp_path)
    store.add(Task(id="T-DISP", title="x", status="in_progress"))

    real_update = store.update

    def selective_boom(task_id, **kwargs):
        # Crash only at the assignment projection so the assign/dispatch events
        # (appended just before it) are the thing under test.
        if "assigned_to" in kwargs:
            raise RuntimeError("crash during assignment projection")
        return real_update(task_id, **kwargs)

    store.update = selective_boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        _apply_checkpoint(
            store, writer, _dispatch_checkpoint(), events=[], gate_dispatcher=None
        )

    types = [e.type for e in log.read_all()]
    # Event-first: the assign + dispatch events were appended before the crash.
    assert "task.assigned" in types
    assert "task.dispatched" in types
