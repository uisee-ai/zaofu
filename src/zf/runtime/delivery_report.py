"""Delivery report projection — DAG completion summary (doc 69 §13, slice S-g).

`delivery-report.v1` = a `delivery-trace.v1` frozen at a terminal point plus a
post-mortem layer with completion-only metrics (duration, first-pass-yield,
rework/pause totals, per-phase summary, verdict, ship result). Pure projection
over a built trace + events; reconstructable from events, never a second truth
(守 I1/I2/I7). Deterministic metrics only; any LLM "lessons" must be marked
proposal-only by the caller (not produced here).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj

EventSlice = Sequence[tuple[int, ZfEvent]]

_DONE_STATES = {"done", "cancelled"}
_REWORK_TYPES = {"task.rework.requested", "task.fix_spawned"}
_EVIDENCE_REF_TYPES = {
    "judge.passed", "judge.failed", "test.passed", "test.failed",
    "review.approved", "review.rejected", "discriminator.passed",
    "discriminator.failed", "ship.completed", "ship.done",
}


def build_delivery_report(
    *,
    trace: dict[str, Any],
    events: EventSlice = (),
    generated_at: str = "",
) -> dict[str, Any]:
    """Compose delivery-report.v1 from a built delivery-trace + events."""
    eg = trace.get("execution_graph", {})
    nodes = eg.get("nodes", [])
    phases = trace.get("phases", [])
    ship = trace.get("ship", {})

    rework_by_task = _rework_by_task(events)
    done_nodes = [n for n in nodes if _status(n) in _DONE_STATES]
    first_pass = sum(1 for n in done_nodes if not rework_by_task.get(n["task_id"]))
    first_pass_yield = round(first_pass / len(done_nodes), 4) if done_nodes else None

    post_mortem = {
        "verdict": _verdict(trace, ship),
        "duration_seconds": _duration_seconds(events),
        "rework_episodes": sum(int(p.get("rework_count") or 0) for p in phases),
        "pause_total": sum(int(p.get("paused_count") or 0) for p in phases),
        "first_pass_yield": first_pass_yield,
        "phase_summary": [
            {
                "phase_id": p.get("phase_id"),
                "status": p.get("status"),
                "completion_rate": p.get("completion_rate"),
                "pass_rate": p.get("pass_rate"),
                "verdict": (p.get("eval") or {}).get("verdict"),
                "rework_count": p.get("rework_count"),
            }
            for p in phases
        ],
        "ship": {
            "shipped": ship.get("shipped", False),
            "merge_ref": ship.get("merge_ref", ""),
            "ship_status": ship.get("ship_status", ""),
            "readiness": ship.get("readiness", ship.get("status", "")),
            "release_blockers": ship.get("release_blockers", []),
        },
        "key_evidence_refs": _key_evidence_refs(events),
    }

    return redact_obj({
        "schema_version": "delivery-report.v1",
        "generated_at": generated_at,
        "feature_id": trace.get("feature_id", ""),
        "trace": trace,            # frozen snapshot
        "post_mortem": post_mortem,
    })


def _verdict(trace: dict[str, Any], ship: dict[str, Any]) -> str:
    if ship.get("shipped"):
        return "shipped"
    if ship.get("ship_status") in ("blocked", "conflict", "failed") or ship.get("release_blockers"):
        return "blocked"
    return str(trace.get("status") or "in_progress")


def _rework_by_task(events: EventSlice) -> dict[str, int]:
    out: dict[str, int] = {}
    for _seq, e in events:
        if e.type in _REWORK_TYPES and e.task_id:
            out[str(e.task_id)] = out.get(str(e.task_id), 0) + 1
    return out


def _duration_seconds(events: EventSlice) -> float | None:
    stamps = []
    for _seq, e in events:
        ts = _parse_ts(e.ts)
        if ts is not None:
            stamps.append(ts)
    if len(stamps) < 2:
        return None
    return round((max(stamps) - min(stamps)).total_seconds(), 1)


def _key_evidence_refs(events: EventSlice) -> list[str]:
    refs = [e.id for _seq, e in events if e.type in _EVIDENCE_REF_TYPES and e.id]
    return refs[-40:]


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts))
    except (ValueError, TypeError):
        return None


def _status(node: dict[str, Any]) -> str:
    return str(node.get("actual", {}).get("status") or "")
