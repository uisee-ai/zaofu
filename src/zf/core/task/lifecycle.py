"""Task lifecycle phase derivation — R-TASK-STATE-AXIS-01.

zaofu's `Task.status` field conflates three orthogonal axes:
  - business_status: is the task active or terminal?
  - lifecycle_phase: which pipeline stage is it in?
  - worker_signal: is the assignee idle/busy/stuck?

Worker signals already live in `worker.state.changed` events (separate
file), so they don't belong on Task. Business status is cleanly read
off `Task.status in _TERMINAL_STATES`. The remaining ambiguity — what
*phase* the task is in — is derived here from the most recent stage-
progress event in events.jsonl, NOT stored as a new field. Storing
would require schema migration and risk drift; deriving keeps the
event log as the single source of truth.

Use cases:
  1. Orchestrator briefing — show Layer 2 "task TASK-X phase=test_done"
     instead of just "in_progress", so the LLM knows where to dispatch
     next without re-deriving from event tail itself.
  2. Future stage-driven dispatch — `_dispatch_ready` could route by
     (phase + role.stages) instead of hardcoded reactor handlers.
"""

from __future__ import annotations

from typing import Iterable

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task


# Phase-progress events emitted by workers, ordered as they typically
# fire on the happy path. Map: event.type → phase label.
#
# `phase` here is the *latest milestone reached*, not the *next stage
# expected*. So after `dev.build.done` the phase is "build_done",
# meaning "build is complete; downstream is review's job".
_PHASE_BY_EVENT: dict[str, str] = {
    "arch.proposal.done": "design_done",
    "design.critique.done": "design_critiqued",
    "dev.build.done": "build_done",
    "static_gate.passed": "static_gate_passed",
    "static_gate.failed": "static_gate_failed",
    "static_gate.skipped": "static_gate_skipped",
    "review.approved": "review_approved",
    "review.rejected": "review_rejected",
    "verify.passed": "verify_passed",
    "verify.failed": "verify_failed",
    "test.passed": "test_passed",
    "test.failed": "test_failed",
    "judge.passed": "judge_passed",
    "judge.failed": "judge_failed",
}


def derive_phase(
    task: Task,
    events: Iterable[ZfEvent],
    role_stages: list[str] | None = None,
) -> str | None:
    """Return the most recent lifecycle phase reached for ``task``.

    ``events`` should be ordered oldest-first (the natural order of
    events.jsonl); the last matching event wins. Returns ``None`` when:
      - task.status == "backlog" (no dispatch yet → no phase)
      - no phase-progress event found for this task_id

    ``role_stages`` is accepted for API stability and future use (a
    later sprint may use it to validate that the derived phase is
    actually reachable in the current topology). Currently unused —
    the function is intentionally narrow: it just reads the most
    recent stage event for this task.
    """
    if task.status == "backlog":
        return None
    latest: str | None = None
    target_id = task.id
    for event in events:
        if event.task_id != target_id:
            continue
        phase = _PHASE_BY_EVENT.get(event.type)
        if phase is not None:
            latest = phase
    return latest


__all__ = ["derive_phase"]
