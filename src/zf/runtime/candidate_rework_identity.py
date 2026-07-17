"""Candidate rework identity and generation guards."""

from __future__ import annotations

from typing import Any


_AUTHORITATIVE_GENERATION_EVENTS = frozenset({
    "task_map.ready",
    "product.delivery.wave.ready",
    "candidate.ready",
    "fanout.started",
})


def _pdd_from_event(
    payload: dict,
    target_ref: str,
    *,
    pdd_by_fanout_id: dict[str, str] | None = None,
) -> str:
    pdd = str(payload.get("pdd_id") or "").strip()
    if pdd:
        return pdd
    fanout_id = str(payload.get("fanout_id") or "").strip()
    if fanout_id and pdd_by_fanout_id:
        fanout_pdd = str(pdd_by_fanout_id.get(fanout_id) or "").strip()
        if fanout_pdd:
            return fanout_pdd
    # candidate target_ref looks like "<candidate-prefix>/<PDD>"; the PDD is
    # the last path segment.
    return target_ref.rsplit("/", 1)[-1].strip() if target_ref else ""


def _candidate_generation_stale(
    events: list,
    *,
    event_idx: int,
    event: object,
    payload: dict[str, Any],
    pdd_by_fanout_id: dict[str, str],
) -> bool:
    """A later authoritative run/generation makes this failure audit-only."""

    workflow_run_id = str(payload.get("workflow_run_id") or "").strip()
    generation = str(payload.get("task_map_generation") or "").strip()
    if not (workflow_run_id or generation):
        return False
    pdd_id = _pdd_from_event(
        payload,
        _candidate_scope_ref(payload),
        pdd_by_fanout_id=pdd_by_fanout_id,
    )
    for later in events[event_idx + 1:]:
        if str(getattr(later, "type", "") or "") not in _AUTHORITATIVE_GENERATION_EVENTS:
            continue
        later_payload = getattr(later, "payload", {}) or {}
        if not isinstance(later_payload, dict):
            continue
        later_pdd = _pdd_from_event(
            later_payload,
            _candidate_scope_ref(later_payload),
            pdd_by_fanout_id=pdd_by_fanout_id,
        )
        if pdd_id and later_pdd and pdd_id != later_pdd:
            continue
        later_run = str(later_payload.get("workflow_run_id") or "").strip()
        later_generation = str(
            later_payload.get("task_map_generation") or ""
        ).strip()
        if workflow_run_id and later_run and workflow_run_id != later_run:
            return True
        if generation and later_generation and generation != later_generation:
            return True
    return False


def _pdd_by_fanout_id(events: list) -> dict[str, str]:
    out: dict[str, str] = {}
    for event in events:
        if getattr(event, "type", "") != "fanout.started":
            continue
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            continue
        fanout_id = str(payload.get("fanout_id") or "").strip()
        fanout_pdd = str(payload.get("pdd_id") or "").strip()
        if fanout_id and fanout_pdd:
            out[fanout_id] = fanout_pdd
    return out


def _candidate_scope_ref(payload: dict[str, Any]) -> str:
    return str(
        payload.get("target_ref")
        or payload.get("candidate_ref")
        or payload.get("branch")
        or ""
    ).strip()


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
