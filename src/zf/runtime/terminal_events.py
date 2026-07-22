"""Canonical, run-scoped terminal-state predicates.

Terminal facts are evidence, not a global stop switch.  A terminal may only
quiesce the run that produced it, and later work in that same run reopens the
diagnostic surface.  Keeping this logic here prevents Supervisor,
Autoresearch, Run Manager, and watchdogs from drifting into different notions
of "completed".
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.run_scope import event_run_id, events_for_run, known_run_ids, run_aliases


# Existing task-attempt predicates remain here.  The run-level predicates below
# deliberately extend this module instead of replacing the controller-facing
# contract used by attempt/rework accounting.
LEGACY_ATTEMPT_SUCCESS_EVENTS = frozenset({
    "dev.build.done",
    "task.attempt.succeeded",
})
LEGACY_ATTEMPT_FAILURE_EVENTS = frozenset({
    "dev.failed",
    "dev.blocked",
    "task.attempt.failed",
})
LEGACY_ATTEMPT_DEADLETTER_EVENTS = frozenset({
    "task.attempt.deadlettered",
})

LEGACY_STAGE_PROGRESS_EVENTS = frozenset({
    "dev.build.done",
    "dev.blocked",
    "review.approved",
    "review.rejected",
    "verify.passed",
    "verify.failed",
    "test.passed",
    "test.failed",
    "judge.passed",
    "judge.failed",
    "arch.proposal.done",
    "design.critique.done",
    "gate.failed",
    "discriminator.failed",
})


RUN_TERMINAL_EVENT_TYPES = frozenset({
    "run.goal.completed",
    "run.completed",
    "ship.completed",
    "ship.done",
    "judge.passed",
})

# These facts establish that the previously terminal run has fresh work.  The
# set intentionally contains mechanical lifecycle facts only; projections and
# diagnostics must not reopen a completed run by themselves.
RUN_REOPEN_EVENT_TYPES = frozenset({
    "run.goal.started",
    "task_map.ready",
    "task_map.amended",
    "gap_plan.ready",
    "goal.gap_plan.ready",
    "flow.gap_plan.ready",
    "flow.discovery.requested",
    "flow.discovery.completed",
    "flow.goal.closed",
    "module.parity.gap_plan.ready",
    "module.parity.scan.completed",
    "task.assigned",
    "task.dispatched",
    "fanout.started",
    "fanout.child.dispatched",
    "fanout.child.completed",
    "fanout.child.failed",
    "fanout.aggregate.completed",
    "candidate.ready",
    "candidate.quality.failed",
    "integration.failed",
    "workflow.resume.applied",
    "dev.build.done",
    "dev.failed",
    "dev.blocked",
    "verify.failed",
    "test.failed",
    "judge.failed",
    "run.failed",
    "run.goal.blocked",
    "task.ref.updated",
    "task.ref.rejected",
})


def is_successful_run_terminal(event: ZfEvent) -> bool:
    """Whether an event is a successful run terminal fact."""

    if event.type not in RUN_TERMINAL_EVENT_TYPES:
        return False
    payload = _payload(event)
    if event.type == "judge.passed":
        if str(payload.get("authority") or "") == "compat_projection":
            return False
        # A per-task judge success is normal stage progress.  Only a
        # candidate/run-level judge result may quiesce the whole workflow.
        return not bool(getattr(event, "task_id", "") or payload.get("task_id"))
    if event.type != "run.completed":
        return True
    status = str(
        payload.get("status") or payload.get("completion_status") or ""
    ).strip().lower()
    return status in {"", "passed", "complete", "completed"}


def latest_quiescent_run_terminal(
    events: Iterable[ZfEvent],
    *,
    run_id: str = "",
) -> ZfEvent | None:
    """Return the latest successful terminal when its run has no later work.

    Ambiguous unscoped facts in a shared state directory fail closed.  Legacy
    single-run logs retain their historical behaviour.
    """

    rows = list(events)
    scoped = _scoped_rows(rows, run_id=run_id)
    if scoped is None:
        return None
    for index in range(len(scoped) - 1, -1, -1):
        terminal = scoped[index]
        if not is_successful_run_terminal(terminal):
            continue
        if not _has_effective_reopen(scoped[index + 1:]):
            return terminal
    return None


def terminal_after_event(
    events: Iterable[ZfEvent],
    source_event: ZfEvent,
) -> ZfEvent | None:
    """Return a quiescent terminal later than ``source_event`` in its run."""

    rows = list(events)
    aliases = run_aliases(rows)
    source_run_id = event_run_id(source_event, aliases=aliases)
    scoped = _scoped_rows(rows, run_id=source_run_id)
    if scoped is None:
        return None
    try:
        source_index = next(
            index for index, event in enumerate(scoped)
            if _same_event(event, source_event)
        )
    except StopIteration:
        return None
    for index in range(len(scoped) - 1, source_index, -1):
        terminal = scoped[index]
        if not is_successful_run_terminal(terminal):
            continue
        if not _has_effective_reopen(scoped[index + 1:]):
            return terminal
    return None


def successful_terminal_before_event(
    events: Iterable[ZfEvent],
    source_event: ZfEvent,
) -> ZfEvent | None:
    """Return a successful terminal that fences a later result event."""

    rows = list(events)
    if not any(_same_event(row, source_event) for row in rows):
        rows.append(source_event)
    aliases = run_aliases(rows)
    source_run_id = event_run_id(source_event, aliases=aliases)
    scoped = _scoped_rows(rows, run_id=source_run_id)
    if scoped is None:
        return None
    try:
        source_index = next(
            index for index, event in enumerate(scoped)
            if _same_event(event, source_event)
        )
    except StopIteration:
        return None
    for index in range(source_index - 1, -1, -1):
        terminal = scoped[index]
        if not is_successful_run_terminal(terminal):
            continue
        # A new run is the only event allowed to reuse worker/fanout identities
        # after terminal. A new task-map generation still rejects old results.
        if any(
            event.type == "run.goal.started"
            for event in scoped[index + 1:source_index]
        ):
            return None
        return terminal
    return None


def _has_effective_reopen(events: list[ZfEvent]) -> bool:
    stale_result_ids = {
        str(_payload(event).get("result_event_id") or "")
        for event in events
        if event.type == "fanout.child.stale_completion"
        and str(_payload(event).get("reason") or "") == "run_terminal"
    }
    for event in events:
        if event.type not in RUN_REOPEN_EVENT_TYPES:
            continue
        payload = _payload(event)
        if payload.get("stale") or payload.get("post_terminal_audit"):
            continue
        if event.id and event.id in stale_result_ids:
            continue
        return True
    return False


def event_run_scope(events: Iterable[ZfEvent], event: ZfEvent) -> str:
    """Return the canonical scope for an event, or empty when ambiguous."""

    rows = list(events)
    aliases = run_aliases(rows)
    scope = event_run_id(event, aliases=aliases)
    if scope:
        return scope
    ids = known_run_ids(rows)
    return next(iter(ids)) if len(ids) == 1 else ""


def _scoped_rows(rows: list[ZfEvent], *, run_id: str) -> list[ZfEvent] | None:
    aliases = run_aliases(rows)
    canonical = aliases.get(str(run_id or "").strip(), "")
    if canonical:
        return events_for_run(rows, run_id=canonical)
    # A caller without an explicit scope is allowed only for a single-run or
    # legacy history.  This is intentionally conservative for shared state.
    if len(known_run_ids(rows)) > 1:
        return None
    return rows


def _same_event(left: ZfEvent, right: ZfEvent) -> bool:
    left_id = str(getattr(left, "id", "") or "")
    right_id = str(getattr(right, "id", "") or "")
    return bool(left_id and left_id == right_id) or left is right


def _payload(event: ZfEvent) -> dict[str, Any]:
    payload = getattr(event, "payload", {}) or {}
    return payload if isinstance(payload, dict) else {}


def is_child_success_event(event_type: str) -> bool:
    return event_type.endswith(".child.completed")


def is_child_failure_event(event_type: str) -> bool:
    return event_type.endswith(".child.failed")


def is_task_attempt_success_event(event_type: str) -> bool:
    return event_type in LEGACY_ATTEMPT_SUCCESS_EVENTS or is_child_success_event(event_type)


def is_task_attempt_failure_event(event_type: str) -> bool:
    return event_type in LEGACY_ATTEMPT_FAILURE_EVENTS or is_child_failure_event(event_type)


def is_task_attempt_deadletter_event(event_type: str) -> bool:
    return event_type in LEGACY_ATTEMPT_DEADLETTER_EVENTS


def is_task_attempt_terminal_event(event_type: str) -> bool:
    return (
        is_task_attempt_success_event(event_type)
        or is_task_attempt_failure_event(event_type)
        or is_task_attempt_deadletter_event(event_type)
    )


def task_attempt_terminal_state(event_type: str) -> str:
    if is_task_attempt_success_event(event_type):
        return "succeeded"
    if is_task_attempt_deadletter_event(event_type):
        return "deadlettered"
    return "failed"


def is_stage_progress_event(event_type: str) -> bool:
    return (
        event_type in LEGACY_STAGE_PROGRESS_EVENTS
        or is_child_success_event(event_type)
        or is_child_failure_event(event_type)
    )


__all__ = [
    "LEGACY_ATTEMPT_DEADLETTER_EVENTS",
    "LEGACY_ATTEMPT_FAILURE_EVENTS",
    "LEGACY_ATTEMPT_SUCCESS_EVENTS",
    "LEGACY_STAGE_PROGRESS_EVENTS",
    "RUN_REOPEN_EVENT_TYPES",
    "RUN_TERMINAL_EVENT_TYPES",
    "event_run_scope",
    "is_child_failure_event",
    "is_child_success_event",
    "is_successful_run_terminal",
    "is_stage_progress_event",
    "is_task_attempt_deadletter_event",
    "is_task_attempt_failure_event",
    "is_task_attempt_success_event",
    "is_task_attempt_terminal_event",
    "latest_quiescent_run_terminal",
    "successful_terminal_before_event",
    "task_attempt_terminal_state",
    "terminal_after_event",
]
