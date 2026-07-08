"""Shared runtime terminal-event predicates.

Controller workflows synthesize stage child events as
``{stage}.child.completed`` / ``{stage}.child.failed``. Treating only legacy
writer events as terminal strands generic workflows in projections, liveness,
and rework accounting.
"""

from __future__ import annotations

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
