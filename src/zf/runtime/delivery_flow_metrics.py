"""Per-task flow metrics + workflow archetype for delivery-trace.v1.

2026-06-10 Delivery slice 1: the backbone metrics shared by all three task
archetypes (feature / refactor / bugfix) — queue wait, first response,
wait/active/rework time segments, backedge counts, and per-rework-round gate
convergence. Pure event-derived projection; sibling module so the additions
do not grow delivery_trace.py (oversized-file discipline).

Segment definitions (documented so the UI can state them in tooltips):
- queue_wait: first task event (or nothing) -> first task.dispatched
- first_response: first task.dispatched -> first non-kernel actor event
- rework: each rework-marked dispatch (payload rework_kind / attempt > 1)
  opens a rework span until the next dispatch or the last task event
- active: total task span minus queue_wait minus rework (floored at 0)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from zf.core.events.model import ZfEvent

BACKEDGE_REWORK_KIND = "workflow_stage_backedge"
_KERNEL_ACTORS = {"zf-cli", "orchestrator", "web", ""}
_GATE_PASS_TYPES = {
    "static_gate.passed", "review.approved", "verify.passed",
    "test.passed", "judge.passed", "discriminator.passed",
}
_GATE_FAIL_TYPES = {
    "static_gate.failed", "review.rejected", "verify.failed",
    "test.failed", "judge.failed", "discriminator.failed",
}
_REFACTOR_MARKERS = ("refactor.scan.", "zaofu.refactor.")
_BUGFIX_MARKERS = ("zaofu.bug.", "self_repair", "repair.dispatch", "remediation.")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _seconds_between(start: str | None, end: str | None) -> float | None:
    a, b = _parse_ts(start), _parse_ts(end)
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds())


def derive_workflow_archetype(events: Iterable[ZfEvent]) -> str:
    """feature | refactor | bugfix — inferred from spine event types."""
    archetype = "feature"
    for event in events:
        etype = event.type
        if any(etype.startswith(marker) for marker in _REFACTOR_MARKERS):
            return "refactor"
        if any(marker in etype for marker in _BUGFIX_MARKERS):
            archetype = "bugfix"
    return archetype


def _is_rework_dispatch(event: ZfEvent) -> bool:
    payload = event.payload or {}
    if str(payload.get("rework_kind") or ""):
        return True
    try:
        return int(payload.get("attempt") or 1) > 1
    except (TypeError, ValueError):
        return False


def _task_metrics(evts: list[ZfEvent]) -> dict[str, Any]:
    dispatches = [e for e in evts if e.type == "task.dispatched"]
    first_ts = evts[0].ts
    last_ts = evts[-1].ts
    total = _seconds_between(first_ts, last_ts) or 0.0

    queue_wait = (
        _seconds_between(first_ts, dispatches[0].ts) if dispatches else None
    )
    first_response = None
    if dispatches:
        d0 = dispatches[0]
        for event in evts:
            if event.ts >= d0.ts and event.id != d0.id and event.actor not in _KERNEL_ACTORS:
                first_response = _seconds_between(d0.ts, event.ts)
                break

    rework_seconds = 0.0
    for index, dispatch in enumerate(dispatches):
        if not _is_rework_dispatch(dispatch):
            continue
        round_end = (
            dispatches[index + 1].ts if index + 1 < len(dispatches) else last_ts
        )
        rework_seconds += _seconds_between(dispatch.ts, round_end) or 0.0

    backedge_count = sum(
        1 for e in evts
        if str((e.payload or {}).get("rework_kind") or "") == BACKEDGE_REWORK_KIND
    )

    convergence: list[dict[str, Any]] = []
    for index, dispatch in enumerate(dispatches):
        round_end = dispatches[index + 1].ts if index + 1 < len(dispatches) else None
        passed = failed = 0
        for event in evts:
            if event.ts < dispatch.ts or (round_end is not None and event.ts >= round_end):
                continue
            if event.type in _GATE_PASS_TYPES:
                passed += 1
            elif event.type in _GATE_FAIL_TYPES:
                failed += 1
        convergence.append({"round": index + 1, "passed": passed, "failed": failed})

    active = max(0.0, total - (queue_wait or 0.0) - rework_seconds)
    return {
        "queue_wait_seconds": queue_wait,
        "first_response_seconds": first_response,
        "wait_seconds": queue_wait,
        "active_seconds": active,
        "rework_seconds": rework_seconds,
        "backedge_count": backedge_count,
        "convergence": convergence,
    }


def build_delivery_flow_metrics(
    events: Iterable[ZfEvent | tuple[int, ZfEvent]],
) -> dict[str, Any]:
    # Accept both bare events and the delivery pipeline's EventSlice
    # (seq, event) tuples so the same builder serves tests and runtime.
    by_task: dict[str, list[ZfEvent]] = {}
    ordered = [item[1] if isinstance(item, tuple) else item for item in events]
    for event in ordered:
        if event.task_id:
            by_task.setdefault(event.task_id, []).append(event)
    return {
        "workflow_archetype": derive_workflow_archetype(ordered),
        "tasks": {tid: _task_metrics(evts) for tid, evts in by_task.items()},
    }
