"""Trace-level score / deposition / observability-ref summaries (doc 82 P3).

Read-only aggregations over already-built ``autoresearch_cycles`` and the
``replan_contract_gate`` projection. Never re-judges kernel verdicts; the
``owner_decision_required`` flag only surfaces gate states that are already
terminal-blocked in the event stream.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from zf.runtime.delivery_projection_common import EventSlice

_OWNER_DECISION_GATE_STATUSES = {"blocked", "stale_rejected"}
_OWNER_DECISION_EVAL_DECISIONS = {"owner_decision", "needs_owner"}


def build_score_summary(autoresearch_cycles: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [
        cycle for cycle in autoresearch_cycles
        if any(cycle.get(key) is not None
               for key in ("score_delta", "baseline_score", "candidate_score"))
    ]
    latest = scored[-1] if scored else {}
    with_delta = [c for c in scored if c.get("score_delta") is not None]
    best = max(with_delta, key=lambda c: c["score_delta"], default={})
    return {
        "schema_version": "delivery-score-summary.v1",
        "scored_cycle_count": len(scored),
        "latest": _score_entry(latest),
        "best": _score_entry(best),
    }


def build_deposition_summary(
    autoresearch_cycles: list[dict[str, Any]],
    replan_contract_gate: dict[str, Any] | None,
) -> dict[str, Any]:
    depositions = [
        str(cycle.get("deposition") or "")
        for cycle in autoresearch_cycles
        if str(cycle.get("deposition") or "").strip()
    ]
    gate = replan_contract_gate or {}
    gate_status = str(gate.get("status") or "none")
    latest_eval = gate.get("latest_eval") if isinstance(gate.get("latest_eval"), dict) else {}
    decision = str(latest_eval.get("decision") or "")
    return {
        "schema_version": "delivery-deposition-summary.v1",
        "counts": dict(Counter(depositions)),
        "latest_deposition": depositions[-1] if depositions else "",
        "replan_gate_status": gate_status,
        "replan_eval_decision": decision,
        "owner_decision_required": (
            gate_status in _OWNER_DECISION_GATE_STATUSES
            or decision in _OWNER_DECISION_EVAL_DECISIONS
        ),
    }


def build_observability_refs(
    events: EventSlice,
    *,
    trace_id: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Diagnostics deep-link refs: one entry per distinct correlation/trace id."""

    by_trace: dict[str, dict[str, Any]] = {}
    for _seq, event in events:
        key = str(event.correlation_id or "") or str(trace_id or "")
        if not key:
            continue
        entry = by_trace.setdefault(key, {
            "kind": "trace",
            "trace_id": key,
            "event_count": 0,
            "last_event_id": "",
            "last_event_type": "",
        })
        entry["event_count"] += 1
        entry["last_event_id"] = event.id
        entry["last_event_type"] = event.type
    ordered = list(by_trace.values())
    if trace_id and trace_id in by_trace:
        ordered.sort(key=lambda item: item["trace_id"] != trace_id)
    return ordered[:limit]


def _score_entry(cycle: dict[str, Any]) -> dict[str, Any]:
    if not cycle:
        return {}
    return {
        "cycle_id": str(cycle.get("cycle_id") or ""),
        "baseline_score": cycle.get("baseline_score"),
        "candidate_score": cycle.get("candidate_score"),
        "score_delta": cycle.get("score_delta"),
        "status": str(cycle.get("status") or ""),
    }
