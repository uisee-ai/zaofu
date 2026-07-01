"""task-lifecycle.v1 — Airflow-style task-instance state history (S-A).

One canonical state trajectory per task, reconstructed purely from
events.jsonl, so every view (trajectory row, Grid, Gantt, swimlane, span
tree) renders the SAME history instead of re-interpreting raw events.

State mapping (Airflow -> ZaoFu, see 2026-06-11 task doc):
  none->backlog  scheduled->ready  queued->dispatched  running->in_progress
  success->done(gate-closed)  failed->gate failed  up_for_retry->rework try
  upstream_failed->blocked  removed->superseded (handled by task_map_history)
"""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.runtime.delivery_flow_metrics import (
    _KERNEL_ACTORS,
    _GATE_FAIL_TYPES,
    _GATE_PASS_TYPES,
    _seconds_between,
)

SCHEMA_VERSION = "task-lifecycle.v1"

# Event type -> lifecycle state entered. Worker first-response promotes
# queued -> running separately (actor-based, not type-based).
_TRANSITIONS: dict[str, str] = {
    "task.created": "backlog",
    "task.ready": "ready",
    "task.requeued": "ready",
    "task.dispatched": "queued",
    "dev.build.done": "verify",
    "task.blocked": "blocked",
    "task.done": "done",
    "judge.passed": "done",
}


def _normalize(
    events: Iterable[ZfEvent | tuple[int, ZfEvent]],
) -> list[tuple[int | None, ZfEvent]]:
    # Preserve the EventSlice seq when the caller has it — it is the cross-view
    # anchor (Observability Events / Run Graph / drawer all sort by it).
    out: list[tuple[int | None, ZfEvent]] = []
    for item in events:
        if isinstance(item, tuple):
            out.append((int(item[0]), item[1]))
        else:
            out.append((None, item))
    return out


def _gate_detail(payload: dict[str, Any]) -> dict[str, Any]:
    # Trimmed evidence summary for the drawer: scalars only, bounded.
    detail: dict[str, Any] = {}
    for key, value in (payload or {}).items():
        if isinstance(value, (str, int, float, bool)) and len(detail) < 6:
            detail[key] = value if not isinstance(value, str) else value[:160]
    return detail


def _task_trajectory(pairs: list[tuple[int | None, ZfEvent]]) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    tries: list[dict[str, Any]] = []
    try_number = 0
    running_seen_for_try = False

    def push(state: str, event: ZfEvent) -> None:
        if history and history[-1]["state"] == state:
            return
        if history:
            history[-1]["dwell_seconds"] = _seconds_between(
                history[-1]["entered_at"], event.ts
            )
        history.append({
            "state": state,
            "entered_at": event.ts,
            "dwell_seconds": None,
            "via_event_id": event.id,
            "try": try_number or None,
        })

    def _touch_seq(seq: int | None) -> None:
        if seq is None or not tries:
            return
        entry = tries[-1]
        if entry["seq_first"] is None:
            entry["seq_first"] = seq
        entry["seq_last"] = seq

    for seq, event in pairs:
        _touch_seq(seq)
        if event.type == "task.dispatched":
            try_number += 1
            running_seen_for_try = False
            payload = event.payload or {}
            tries.append({
                "try": try_number,
                "dispatched_at": event.ts,
                "first_response_seconds": None,
                "outcome": "in_flight",
                "gate_results": [],
                "rework_kind": str(payload.get("rework_kind") or "") or None,
                # trace anchors (2026-06-11 T-knife 1): one-time dispatch token,
                # the briefing the worker actually received, and the state
                # snapshot it resumed from — straight off the dispatch payload.
                "dispatch_id": str(payload.get("dispatch_id") or "") or None,
                "briefing_ref": str(payload.get("briefing") or "") or None,
                "snapshot_ref": str(payload.get("snapshot_ref") or "") or None,
                "seq_first": seq,
                "seq_last": seq,
                "tool_calls": 0,
                "tokens_in": 0,
                "tokens_out": 0,
            })
            push("queued", event)
            continue
        if event.type == "codex.hook.post_tool_use" and tries:
            tries[-1]["tool_calls"] += 1
            continue
        if event.type == "agent.usage" and tries:
            usage = (event.payload or {}).get("usage") or {}
            try:
                tries[-1]["tokens_in"] += int(usage.get("input_tokens") or 0)
                tries[-1]["tokens_out"] += int(usage.get("output_tokens") or 0)
            except (TypeError, ValueError):
                pass
            continue
        if event.type in _TRANSITIONS:
            # A worker-authored transition (e.g. dev.build.done with no prior
            # heartbeat) is also the try's first response: enter running at
            # the same instant, then the mapped state.
            if (
                tries
                and not running_seen_for_try
                and event.actor not in _KERNEL_ACTORS
            ):
                running_seen_for_try = True
                tries[-1]["first_response_seconds"] = _seconds_between(
                    tries[-1]["dispatched_at"], event.ts
                )
                push("running", event)
            state = _TRANSITIONS[event.type]
            push(state, event)
            if tries and state in {"done", "blocked"}:
                tries[-1]["outcome"] = state
            continue
        if event.type in _GATE_PASS_TYPES or event.type in _GATE_FAIL_TYPES:
            passed = event.type in _GATE_PASS_TYPES
            if tries:
                tries[-1]["gate_results"].append({
                    "type": event.type,
                    "passed": passed,
                    "event_id": event.id,
                    "detail": _gate_detail(event.payload or {}),
                })
            if not passed:
                push("failed", event)
                if tries:
                    tries[-1]["outcome"] = "failed"
            elif event.type == "judge.passed":
                push("done", event)
            continue
        if (
            tries
            and not running_seen_for_try
            and event.actor not in _KERNEL_ACTORS
        ):
            running_seen_for_try = True
            tries[-1]["first_response_seconds"] = _seconds_between(
                tries[-1]["dispatched_at"], event.ts
            )
            push("running", event)

    return {"state_history": history, "tries": tries}


def build_task_lifecycle(
    events: Iterable[ZfEvent | tuple[int, ZfEvent]],
) -> dict[str, Any]:
    by_task: dict[str, list[tuple[int | None, ZfEvent]]] = {}
    for seq, event in _normalize(events):
        if event.task_id:
            by_task.setdefault(event.task_id, []).append((seq, event))
    return {
        "schema_version": SCHEMA_VERSION,
        "tasks": {tid: _task_trajectory(pairs) for tid, pairs in by_task.items()},
    }
