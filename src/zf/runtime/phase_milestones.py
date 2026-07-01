"""Phase milestone computation (doc 69 §3.3 / slice S-f).

Pure, deterministic, idempotent computation of which phase-milestone events
should be emitted given the current phase rollups and the milestones already
in the event log. The KERNEL emits these (mechanical transition from events);
this module only computes — it writes nothing (守 I1/I3).

Milestones:
  delivery.phase.started    — phase has progressed past 'waiting'
  delivery.phase.evaluated  — every task in the phase is terminal (completion==1.0)
  delivery.phase.completed  — evaluated AND not failed (verdict != 'fail')

Idempotency: a milestone is only returned if (type, feature_id, phase_id) is not
already present in `emitted_keys`, so re-running never double-emits.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent

EventSlice = Sequence[tuple[int, ZfEvent]]

_MILESTONE_TYPES = (
    "delivery.phase.started",
    "delivery.phase.evaluated",
    "delivery.phase.completed",
)
_STARTED_STATES = {"in_progress", "rework", "blocked", "done"}


def emitted_milestone_keys(events: EventSlice) -> set[tuple[str, str, str]]:
    """(type, feature_id, phase_id) for every milestone already in the log."""
    keys: set[tuple[str, str, str]] = set()
    for _seq, e in events:
        if e.type not in _MILESTONE_TYPES:
            continue
        p = e.payload if isinstance(e.payload, dict) else {}
        keys.add((e.type, str(p.get("feature_id") or ""), str(p.get("phase_id") or "")))
    return keys


def compute_phase_milestones(
    *,
    feature_id: str,
    phases: list[dict[str, Any]],
    emitted_keys: set[tuple[str, str, str]],
) -> list[tuple[str, dict[str, Any]]]:
    """Return [(event_type, payload)] for milestones not yet emitted."""
    out: list[tuple[str, dict[str, Any]]] = []

    def _new(event_type: str, phase_id: str, payload: dict[str, Any]) -> None:
        if (event_type, feature_id, phase_id) in emitted_keys:
            return
        out.append((event_type, {"feature_id": feature_id, "phase_id": phase_id, **payload}))

    for ph in phases:
        pid = str(ph.get("phase_id") or "")
        status = str(ph.get("status") or "")
        completion = ph.get("completion_rate")
        pass_rate = ph.get("pass_rate")
        verdict = (ph.get("eval") or {}).get("verdict")

        if status in _STARTED_STATES:
            _new("delivery.phase.started", pid, {})
        if completion == 1.0:
            _new("delivery.phase.evaluated", pid, {
                "completion_rate": completion, "pass_rate": pass_rate, "verdict": verdict,
            })
            if verdict != "fail":
                _new("delivery.phase.completed", pid, {
                    "completion_rate": completion, "pass_rate": pass_rate,
                })
    return out
