"""TR-DISPATCH-SILENT-STALL-001 (2026-05-21): dispatch silent stall sweep.

#G silent_stall site 6 (cangjie 2026-05-21 Round 1).

Builds on commit 4de1ff7 (5 _dispatch_ready internal silent-stall
sites). The 4de1ff7 fix covers silent skips *inside* the dispatch
loop. This sweep covers the orthogonal case: kernel reactor doesn't
re-tick after task.assigned (no new events to fire it), so
task.assigned can sit without a matching task.dispatched
indefinitely.

Time-window sweep:
  - For each (task_id, assignee) pair, find latest task.assigned ts
  - If no matching task.dispatched (ts >= assigned_ts) is seen,
    and (now - assigned_ts) >= silent_stall_threshold_s,
    classify as silent_stall.

Pure / testable: this module just classifies events and returns a
result. The orchestrator wraps the call and emits dispatch.silent_stall
events for each entry.

Backlog: backlogs/2026-05-21-0821-zaofu-silent-stall-site-6-task-
         assigned-no-dispatched.md
Cangjie evidence: /path/to/example-project/.zf/events-r1-
                  final.jsonl.bak (L179 task.assigned role=dev
                  @ 06:56:00, 30+ min no task.dispatched, 9+ min
                  silent before operator-driven detection).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from zf.core.events.model import ZfEvent


_DEFAULT_SILENT_STALL_THRESHOLD_S = 30.0


@dataclass(frozen=True)
class DispatchSweepResult:
    """Outcome of one dispatch sweep pass.

    silent_stalls: list of (task_id, assignee, age_seconds) tuples
    for pairs whose latest task.assigned has no matching
    task.dispatched within the threshold window. Sorted by
    (task_id, assignee) for stable test assertions.
    """

    silent_stalls: list[tuple[str, str, float]] = field(default_factory=list)


def _parse_ts(value) -> datetime | None:
    """Best-effort ISO8601 parse. Returns None on failure (never raises).

    Mirrors heartbeat_sweep._parse_ts for consistency.
    """
    if not isinstance(value, str) or not value:
        return None
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _assignee_of(payload: dict) -> str:
    """Resolve assignee identity from event payload.

    Mirrors orchestrator_dispatch._assignee_equivalent precedence:
    `assignee` (instance_id) preferred, fall back to `role` (type).
    """
    assignee = payload.get("assignee")
    if isinstance(assignee, str) and assignee.strip():
        return assignee.strip()
    role = payload.get("role")
    if isinstance(role, str) and role.strip():
        return role.strip()
    return ""


def _dispatch_assignee_keys(payload: dict) -> set[str]:
    """All assignee identities satisfied by one task.dispatched event.

    Dispatch may assign a role-level request (``dev``) to a concrete replica
    (``dev-1``). A role-level ``task.assigned`` is fulfilled by that instance
    dispatch, while an explicit instance assignment (``dev-2``) is not.
    """
    keys: set[str] = set()
    assignee = payload.get("assignee")
    if isinstance(assignee, str) and assignee.strip():
        keys.add(assignee.strip())
    role = payload.get("role")
    if isinstance(role, str) and role.strip():
        keys.add(role.strip())
    return keys


def sweep_silent_dispatches(
    *,
    events: Iterable[ZfEvent],
    now: datetime | None = None,
    silent_stall_threshold_s: float = _DEFAULT_SILENT_STALL_THRESHOLD_S,
) -> DispatchSweepResult:
    """Find (task_id, assignee) pairs where task.assigned has no
    matching task.dispatched within the threshold window.

    Idempotent + side-effect-free. The orchestrator decides what to do
    with the result (typically: emit `dispatch.silent_stall` event for
    each entry, optionally re-trigger _dispatch_ready).

    C3 reassign pattern (same task_id, different assignee in sequence)
    is supported — each (task_id, assignee) pair is checked
    independently. Cangjie Round 1 #G scenario corresponds to:
    task.assigned role=dev (review-1 reassign) without matching
    task.dispatched.
    """
    sweep_now = now or datetime.now(timezone.utc)

    # latest_assigned: (task_id, assignee) -> latest task.assigned ts
    latest_assigned: dict[tuple[str, str], datetime] = {}
    # dispatched_seen: (task_id, assignee) for which task.dispatched
    # has been observed AFTER the latest task.assigned.
    dispatched_seen: set[tuple[str, str]] = set()
    # Any prior dispatch for the same task/assignee. A duplicate manual
    # task.assigned without a fresh dispatch_id should not invalidate an
    # already-running worker; otherwise an operator no-op reassign creates a
    # false silent_stall while the original dispatch is still executing.
    ever_dispatched: set[tuple[str, str]] = set()

    for ev in events:
        if ev.type not in {"task.assigned", "task.dispatched"}:
            continue
        task_id = (ev.task_id or "").strip()
        if not task_id:
            continue
        assignee = _assignee_of(ev.payload or {})
        if not assignee:
            continue
        ts = _parse_ts(ev.ts)
        if ts is None:
            continue
        if ev.type == "task.assigned":
            key = (task_id, assignee)
            prev = latest_assigned.get(key)
            if prev is None or ts > prev:
                latest_assigned[key] = ts
                if key in ever_dispatched and not (ev.payload or {}).get("dispatch_id"):
                    dispatched_seen.add(key)
                else:
                    # Reset dispatched flag — caller must see a new
                    # task.dispatched AFTER this reassignment to clear.
                    dispatched_seen.discard(key)
        else:  # task.dispatched
            for dispatched_assignee in _dispatch_assignee_keys(ev.payload or {}):
                key = (task_id, dispatched_assignee)
                ever_dispatched.add(key)
                assigned_ts = latest_assigned.get(key)
                if assigned_ts is not None and ts >= assigned_ts:
                    dispatched_seen.add(key)

    silent_stalls: list[tuple[str, str, float]] = []
    for (task_id, assignee), assigned_ts in latest_assigned.items():
        if (task_id, assignee) in dispatched_seen:
            continue
        age = (sweep_now - assigned_ts).total_seconds()
        if age >= silent_stall_threshold_s:
            silent_stalls.append((task_id, assignee, age))

    silent_stalls.sort()
    return DispatchSweepResult(silent_stalls=silent_stalls)
