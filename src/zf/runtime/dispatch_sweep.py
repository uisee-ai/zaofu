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
_DEFAULT_DEAD_DISPATCH_THRESHOLD_S = 180.0


@dataclass(frozen=True)
class DeadDispatchSweepResult:
    """Outcome of one dead-dispatch sweep pass (ZF-E2E-RACING-P1).

    dead_dispatches: list of (task_id, assignee, dispatch_id,
    silent_age_seconds) for in-flight tasks whose assignee shows zero
    event activity for the threshold window — task.dispatched exists but
    the worker session died (process restart / pane reset), the one shape
    sweep_silent_dispatches cannot see.
    """

    dead_dispatches: list[tuple[str, str, str, float]] = field(
        default_factory=list
    )


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
    role_instance = payload.get("role_instance")
    if isinstance(role_instance, str) and role_instance.strip():
        keys.add(role_instance.strip())
    assigned_to = payload.get("assigned_to")
    if isinstance(assigned_to, str) and assigned_to.strip():
        keys.add(assigned_to.strip())
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
    # dispatched_seen: (task_id, assignee) for which task.dispatched or
    # fanout.child.dispatched
    # has been observed AFTER the latest task.assigned.
    dispatched_seen: set[tuple[str, str]] = set()
    # Any prior dispatch for the same task/assignee. A duplicate manual
    # task.assigned without a fresh dispatch_id should not invalidate an
    # already-running worker; otherwise an operator no-op reassign creates a
    # false silent_stall while the original dispatch is still executing.
    ever_dispatched: set[tuple[str, str]] = set()

    for ev in events:
        if ev.type not in {"task.assigned", "task.dispatched", "fanout.child.dispatched"}:
            continue
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        task_id = (ev.task_id or str(payload.get("task_id") or "")).strip()
        if not task_id:
            continue
        ts = _parse_ts(ev.ts)
        if ts is None:
            continue
        if ev.type == "task.assigned":
            assignee = _assignee_of(payload)
            if not assignee:
                continue
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
        else:  # task.dispatched / fanout.child.dispatched
            for dispatched_assignee in _dispatch_assignee_keys(payload):
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


def sweep_dead_dispatches(
    *,
    inflight: Iterable[tuple[str, str, str]],
    events: Iterable[ZfEvent],
    now: datetime | None = None,
    dead_threshold_s: float = _DEFAULT_DEAD_DISPATCH_THRESHOLD_S,
    assignee_thresholds_s: dict[str, float] | None = None,
    progressed_task_ids: set[str] | None = None,
) -> DeadDispatchSweepResult:
    """ZF-E2E-RACING-P1 (2026-07-11): find in-flight dispatches whose worker
    went silent — the shape sweep_silent_dispatches cannot see.

    inflight: (task_id, assignee, active_dispatch_id) triples from the task
    store (status=in_progress with a non-empty active_dispatch_id). For each,
    the latest event either referencing the task or emitted by the assignee
    actor (instance or its role prefix) counts as life. No life for
    dead_threshold_s → dead. If the pair never appears in the window, age is
    floored at the window's earliest event, and short windows (< threshold)
    are skipped to avoid false positives.

    Racing e2e evidence: review dispatched 06:16:39, runtime restart 06:23
    reset the pane; drift refresh spun no-op every 30s and nothing redrove
    the dispatch until an operator re-assign at 06:29.
    """
    sweep_now = now or datetime.now(timezone.utc)

    earliest_ts: datetime | None = None
    last_task_activity: dict[str, datetime] = {}
    last_actor_activity: dict[str, datetime] = {}
    for ev in events:
        ts = _parse_ts(ev.ts)
        if ts is None:
            continue
        if earliest_ts is None or ts < earliest_ts:
            earliest_ts = ts
        task_id = (ev.task_id or "").strip()
        if task_id:
            prev = last_task_activity.get(task_id)
            if prev is None or ts > prev:
                last_task_activity[task_id] = ts
        actor = (ev.actor or "").strip()
        if actor:
            prev = last_actor_activity.get(actor)
            if prev is None or ts > prev:
                last_actor_activity[actor] = ts

    dead: list[tuple[str, str, str, float]] = []
    progressed = progressed_task_ids or set()
    thresholds = assignee_thresholds_s or {}
    for task_id, assignee, dispatch_id in inflight:
        if not task_id or not assignee or not dispatch_id:
            continue
        # PRD e2e calibration (2026-07-11): fanout-lane tasks legitimately
        # stay in-flight AFTER completing their build while the flow waits
        # for verify/judge. The wedge shape is dispatched-but-NEVER-
        # progressed; completion evidence excludes a task from dead
        # judgment (false stalls here fed RM workflow_resume, which
        # re-reworked three healthy, candidate-assembled tasks).
        if task_id in progressed:
            continue
        candidates = [last_task_activity.get(task_id)]
        for actor, ts in last_actor_activity.items():
            if actor == assignee or actor.startswith(f"{assignee}-"):
                candidates.append(ts)
        alive = [ts for ts in candidates if ts is not None]
        if alive:
            last_seen = max(alive)
        elif earliest_ts is not None:
            # Pair absent from the window entirely: only judge when the
            # window itself spans the threshold.
            if (sweep_now - earliest_ts).total_seconds() < dead_threshold_s:
                continue
            last_seen = earliest_ts
        else:
            continue
        age = (sweep_now - last_seen).total_seconds()
        threshold = max(float(thresholds.get(assignee, dead_threshold_s)), 0.0)
        if age >= threshold:
            dead.append((task_id, assignee, dispatch_id, age))

    dead.sort()
    return DeadDispatchSweepResult(dead_dispatches=dead)
