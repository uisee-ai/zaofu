"""Deterministic diagnosis rows for ``loop.v1``.

The diagnosis layer explains why a loop candidate points at a particular
repair surface. It is derived only from loop/event refs and never mutates
runtime truth.
"""

from __future__ import annotations

import hashlib
from typing import Any


_RULES: dict[str, dict[str, object]] = {
    "gate_failure": {
        "fix_layer": "gate_evidence",
        "recommended_action": "review_gate_evidence",
        "confidence": 0.86,
        "reason": "gate failure requires evidence or contract review",
    },
    "missing_evidence": {
        "fix_layer": "gate_evidence",
        "recommended_action": "harden_evidence_contract",
        "confidence": 0.9,
        "reason": "required evidence is missing from completion payload",
    },
    "stuck_worker": {
        "fix_layer": "agent_runtime",
        "recommended_action": "inspect_worker_liveness",
        "confidence": 0.88,
        "reason": "worker heartbeat or probe indicates stalled execution",
    },
    "fanout_retry": {
        "fix_layer": "workflow",
        "recommended_action": "review_fanout_barrier",
        "confidence": 0.78,
        "reason": "fanout retry indicates workflow barrier or child-run instability",
    },
    "autoresearch": {
        "fix_layer": "autoresearch",
        "recommended_action": "review_autoresearch_result",
        "confidence": 0.74,
        "reason": "autoresearch loop needs proposal or reflection review",
    },
    "replan": {
        "fix_layer": "replan",
        "recommended_action": "review_replan_contract",
        "confidence": 0.82,
        "reason": "replan contract or candidate task map requires evaluation",
    },
    "rework": {
        "fix_layer": "task_contract",
        "recommended_action": "inspect_rework_route",
        "confidence": 0.72,
        "reason": "rework route should be checked against task contract",
    },
}


def attach_loop_diagnoses(
    *,
    loops: dict[str, dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach diagnosis refs to loop and candidate rows."""

    by_loop_id = {
        str(loop.get("loop_id") or ""): loop
        for loop in loops.values()
        if str(loop.get("loop_id") or "")
    }
    diagnoses: dict[str, dict[str, Any]] = {}
    for loop in loops.values():
        row = _diagnosis_for(loop)
        diagnoses[row["diagnosis_id"]] = row
        loop["diagnosis_id"] = row["diagnosis_id"]
        loop["fix_layer"] = row["fix_layer"]

    for candidate in candidates.values():
        loop = by_loop_id.get(str(candidate.get("loop_id") or ""))
        if loop is None:
            continue
        row = _diagnosis_for(loop, candidate)
        diagnoses[row["diagnosis_id"]] = row
        candidate["diagnosis_id"] = row["diagnosis_id"]
        candidate["diagnosis"] = row
        candidate["suggested_action"] = row["recommended_action"]
        candidate["fix_layer"] = row["fix_layer"]

    return sorted(diagnoses.values(), key=lambda item: str(item.get("diagnosis_id") or ""))


def _diagnosis_for(
    loop: dict[str, Any],
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_kind = str((candidate or {}).get("source_kind") or loop.get("kind") or "unknown")
    rule = _RULES.get(source_kind, _unknown_rule())
    event_refs = _string_list((candidate or {}).get("event_ids")) or _string_list(loop.get("event_ids"))
    task_refs = _string_list((candidate or {}).get("task_ids")) or _string_list(loop.get("task_ids"))
    source_event_types = _string_list(loop.get("source_event_types"))
    diagnosis_id = f"diagnosis:{source_kind}:{_stable_id(loop.get('loop_id'), ','.join(event_refs))}"
    return {
        "diagnosis_id": diagnosis_id,
        "loop_id": str(loop.get("loop_id") or ""),
        "candidate_id": str((candidate or {}).get("candidate_id") or ""),
        "source_kind": source_kind,
        "fix_layer": str(rule["fix_layer"]),
        "confidence": float(rule["confidence"]),
        "reason": str(rule["reason"]),
        "recommended_action": str(rule["recommended_action"]),
        "evidence_refs": event_refs,
        "source_event_types": source_event_types,
        "secondary_signals": _secondary_signals(source_kind, source_event_types),
        "evidence_packet": {
            "event_refs": event_refs,
            "task_refs": task_refs,
            "feature_refs": _string_list(loop.get("feature_ids")),
            "fanout_refs": _string_list(loop.get("fanout_ids")),
            "trace_refs": _string_list(loop.get("trace_ids")),
            "source_event_types": source_event_types,
        },
    }


def _unknown_rule() -> dict[str, object]:
    return {
        "fix_layer": "unknown",
        "recommended_action": "inspect_loop",
        "confidence": 0.45,
        "reason": "loop signal has no deterministic diagnosis rule",
    }


def _secondary_signals(source_kind: str, event_types: list[str]) -> list[str]:
    out: list[str] = []
    for event_type in event_types:
        if source_kind and source_kind in event_type:
            continue
        if event_type.endswith(".failed"):
            out.append("failure")
        elif event_type.endswith(".passed"):
            out.append("recovery")
        elif "rework" in event_type:
            out.append("rework")
        elif "worker" in event_type:
            out.append("worker")
        elif "replan" in event_type:
            out.append("replan")
    return _dedupe(out)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _stable_id(*parts: object) -> str:
    raw = ":".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
