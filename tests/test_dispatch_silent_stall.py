"""TR-DISPATCH-SILENT-STALL-001: dispatch silent stall watchdog.

#G silent_stall site 6 (cangjie 2026-05-21 Round 1): when kernel
reactor doesn't re-tick after task.assigned (no new events to fire
it), task.assigned can sit without matching task.dispatched
indefinitely. Sweep detects by time-window and emits
dispatch.silent_stall.

Backlog: backlogs/2026-05-21-0821-zaofu-silent-stall-site-6-task-assigned-no-dispatched.md
Cangjie evidence: /path/to/example-project/.zf/events-r1-final.jsonl.bak
                  (L179 task.assigned role=dev @ 06:56:00,
                   30+ min no task.dispatched)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from zf.core.events.model import ZfEvent


def _evt(
    type_: str,
    ts: datetime,
    task_id: str = "",
    payload: dict | None = None,
) -> ZfEvent:
    return ZfEvent(
        type=type_,
        actor="zf-cli",
        task_id=task_id,
        payload=payload or {},
        ts=ts.isoformat(),
    )


# ─── event type registration ─────────────────────────────────────────────


def test_dispatch_silent_stall_in_known_types():
    """schema_lock_test pre-req: dispatch.silent_stall must be known."""
    from zf.core.events.known_types import KNOWN_EVENT_TYPES

    assert "dispatch.silent_stall" in KNOWN_EVENT_TYPES


def test_dispatch_silent_stall_in_wake_patterns():
    """EventWatcher wake pre-req: emit alone doesn't wake LLM agent
    unless event is in WAKE_PATTERNS. Without this, orchestrator pane
    stays at 0 tokens after sweep emit (cangjie 2026-05-21 08:42 obs).
    """
    from zf.runtime.wake_patterns import WAKE_PATTERNS

    assert "dispatch.silent_stall" in WAKE_PATTERNS


# ─── pure sweep classify ─────────────────────────────────────────────────


def test_no_stall_when_dispatched_in_time():
    """task.assigned followed by task.dispatched within threshold → no stall."""
    from zf.runtime.dispatch_sweep import sweep_silent_dispatches

    now = datetime.now(timezone.utc)
    events = [
        _evt("task.assigned",   now - timedelta(seconds=10), "TASK-X", {"assignee": "dev-1"}),
        _evt("task.dispatched", now - timedelta(seconds=5),  "TASK-X", {"assignee": "dev-1"}),
    ]
    result = sweep_silent_dispatches(events=events, now=now)
    assert result.silent_stalls == []


def test_silent_stall_when_assigned_but_no_dispatched_after_threshold():
    """task.assigned > threshold ago + no task.dispatched → silent_stall."""
    from zf.runtime.dispatch_sweep import sweep_silent_dispatches

    now = datetime.now(timezone.utc)
    events = [
        _evt("task.assigned", now - timedelta(seconds=45), "TASK-X", {"assignee": "dev-1"}),
    ]
    result = sweep_silent_dispatches(
        events=events, now=now, silent_stall_threshold_s=30.0,
    )
    assert len(result.silent_stalls) == 1
    task_id, assignee, age = result.silent_stalls[0]
    assert task_id == "TASK-X"
    assert assignee == "dev-1"
    assert age >= 45.0


def test_no_stall_when_assigned_within_threshold():
    """task.assigned < threshold ago + no task.dispatched → not yet stall."""
    from zf.runtime.dispatch_sweep import sweep_silent_dispatches

    now = datetime.now(timezone.utc)
    events = [
        _evt("task.assigned", now - timedelta(seconds=15), "TASK-X", {"assignee": "dev-1"}),
    ]
    result = sweep_silent_dispatches(
        events=events, now=now, silent_stall_threshold_s=30.0,
    )
    assert result.silent_stalls == []


def test_reassign_redispatch_happy_path():
    """C3 reassign happy path: dev-1 dispatched, then reassign review-1, both in time."""
    from zf.runtime.dispatch_sweep import sweep_silent_dispatches

    now = datetime.now(timezone.utc)
    events = [
        _evt("task.assigned",   now - timedelta(seconds=120), "TASK-X", {"assignee": "dev-1"}),
        _evt("task.dispatched", now - timedelta(seconds=115), "TASK-X", {"assignee": "dev-1"}),
        _evt("task.assigned",   now - timedelta(seconds=20),  "TASK-X", {"assignee": "review-1"}),
        _evt("task.dispatched", now - timedelta(seconds=15),  "TASK-X", {"assignee": "review-1"}),
    ]
    result = sweep_silent_dispatches(
        events=events, now=now, silent_stall_threshold_s=30.0,
    )
    assert result.silent_stalls == []


def test_reassign_silent_stall_on_latest_assignee():
    """C3 reassign stuck on latest: dev-1 OK, but review-1 reassign has no dispatch.

    This is the cangjie Round 1 #G scenario at L179: task.assigned
    role=dev with no follow-up task.dispatched.
    """
    from zf.runtime.dispatch_sweep import sweep_silent_dispatches

    now = datetime.now(timezone.utc)
    events = [
        _evt("task.assigned",   now - timedelta(seconds=120), "TASK-X", {"assignee": "dev-1"}),
        _evt("task.dispatched", now - timedelta(seconds=115), "TASK-X", {"assignee": "dev-1"}),
        _evt("task.assigned",   now - timedelta(seconds=45),  "TASK-X", {"assignee": "review-1"}),
        # no task.dispatched for review-1 within 30s
    ]
    result = sweep_silent_dispatches(
        events=events, now=now, silent_stall_threshold_s=30.0,
    )
    assert len(result.silent_stalls) == 1
    task_id, assignee, age = result.silent_stalls[0]
    assert task_id == "TASK-X"
    assert assignee == "review-1"
    assert age >= 45.0


def test_duplicate_same_assignee_without_dispatch_id_does_not_stall():
    """A no-op reassignment to an already dispatched worker is not a new dispatch.

    This covers operator/manual assign repair where the worker is already
    executing the task. A fresh dispatch_id still requires a new
    task.dispatched event and remains covered by reassign stall tests.
    """
    from zf.runtime.dispatch_sweep import sweep_silent_dispatches

    now = datetime.now(timezone.utc)
    events = [
        _evt("task.assigned",   now - timedelta(seconds=120), "TASK-X", {"assignee": "arch"}),
        _evt("task.dispatched", now - timedelta(seconds=115), "TASK-X", {"assignee": "arch"}),
        _evt("task.assigned",   now - timedelta(seconds=45),  "TASK-X", {"assignee": "arch"}),
    ]
    result = sweep_silent_dispatches(
        events=events, now=now, silent_stall_threshold_s=30.0,
    )
    assert result.silent_stalls == []


def test_role_assignment_fulfilled_by_instance_dispatch():
    """role=dev assignment is fulfilled when dispatcher picks dev-1."""
    from zf.runtime.dispatch_sweep import sweep_silent_dispatches

    now = datetime.now(timezone.utc)
    events = [
        _evt(
            "task.assigned",
            now - timedelta(seconds=120),
            "TASK-X",
            {"role": "dev", "assignee": "dev"},
        ),
        _evt(
            "task.dispatched",
            now - timedelta(seconds=115),
            "TASK-X",
            {"role": "dev", "assignee": "dev-1"},
        ),
        _evt(
            "task.assigned",
            now - timedelta(seconds=45),
            "TASK-X",
            {"role": "dev", "assignee": "dev"},
        ),
    ]
    result = sweep_silent_dispatches(
        events=events, now=now, silent_stall_threshold_s=30.0,
    )

    assert result.silent_stalls == []


def test_fanout_child_dispatch_fulfills_lane_assignment():
    """Lane fanout dispatch is the real task dispatch for writer fanout runs."""
    from zf.runtime.dispatch_sweep import sweep_silent_dispatches

    now = datetime.now(timezone.utc)
    events = [
        _evt(
            "task.assigned",
            now - timedelta(seconds=90),
            "TASK-X",
            {"assignee": "dev-lane-0"},
        ),
        _evt(
            "fanout.child.dispatched",
            now - timedelta(seconds=85),
            "",
            {
                "task_id": "TASK-X",
                "role_instance": "dev-lane-0",
                "child_id": "dev-lane-0-TASK-X",
                "fanout_id": "fanout-impl-1",
            },
        ),
    ]

    result = sweep_silent_dispatches(
        events=events,
        now=now,
        silent_stall_threshold_s=30.0,
    )

    assert result.silent_stalls == []


def test_instance_assignment_not_fulfilled_by_different_role_replica_dispatch():
    """Explicit dev-2 assignment is not covered by a dev-1 dispatch."""
    from zf.runtime.dispatch_sweep import sweep_silent_dispatches

    now = datetime.now(timezone.utc)
    events = [
        _evt(
            "task.assigned",
            now - timedelta(seconds=45),
            "TASK-X",
            {"role": "dev", "assignee": "dev-2"},
        ),
        _evt(
            "task.dispatched",
            now - timedelta(seconds=40),
            "TASK-X",
            {"role": "dev", "assignee": "dev-1"},
        ),
    ]
    result = sweep_silent_dispatches(
        events=events, now=now, silent_stall_threshold_s=30.0,
    )

    assert len(result.silent_stalls) == 1
    task_id, assignee, _ = result.silent_stalls[0]
    assert task_id == "TASK-X"
    assert assignee == "dev-2"


def test_multi_task_only_stalled_one_reported():
    """2 tasks in parallel — only the one missing dispatch → stall."""
    from zf.runtime.dispatch_sweep import sweep_silent_dispatches

    now = datetime.now(timezone.utc)
    events = [
        _evt("task.assigned",   now - timedelta(seconds=60), "TASK-A", {"assignee": "dev-1"}),
        _evt("task.dispatched", now - timedelta(seconds=55), "TASK-A", {"assignee": "dev-1"}),
        _evt("task.assigned",   now - timedelta(seconds=50), "TASK-B", {"assignee": "dev-2"}),
        # no dispatched for TASK-B
    ]
    result = sweep_silent_dispatches(
        events=events, now=now, silent_stall_threshold_s=30.0,
    )
    assert len(result.silent_stalls) == 1
    task_id, assignee, age = result.silent_stalls[0]
    assert task_id == "TASK-B"
    assert assignee == "dev-2"


def test_empty_events_no_stall():
    """No events at all → no stall (defensive)."""
    from zf.runtime.dispatch_sweep import sweep_silent_dispatches

    now = datetime.now(timezone.utc)
    result = sweep_silent_dispatches(events=[], now=now)
    assert result.silent_stalls == []
