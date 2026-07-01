"""Build deterministic synth-retry events for candidate-level replans."""

from __future__ import annotations

from collections.abc import Sequence

from zf.core.events.model import ZfEvent


def build_replan_resynth_event(
    *,
    plan: object,
    events: Sequence[ZfEvent],
    config: object,
) -> ZfEvent | None:
    """Return the plan-synth trigger for a candidate-level replan."""

    workflow = getattr(config, "workflow", None)
    replan_cfg = getattr(workflow, "admission_replan", None)
    trigger = str(getattr(replan_cfg, "resynth_trigger", "") or "").strip()
    if not getattr(replan_cfg, "enabled", False) or not trigger:
        return None

    base_payload = _latest_trigger_payload(
        events,
        trigger=trigger,
        trace_id=str(getattr(plan, "trace_id", "") or ""),
    )
    payload = dict(base_payload)
    payload.update({
        "pdd_id": getattr(plan, "pdd_id", ""),
        "trace_id": getattr(plan, "trace_id", ""),
        "target_ref": getattr(plan, "target_ref", "") or payload.get("target_ref", ""),
        "rework_of": getattr(plan, "source_event_id", ""),
        "rework_attempt": getattr(plan, "attempt", 0),
        "rework_source": getattr(plan, "source_event_type", ""),
        "rework_feedback": list(getattr(plan, "feedback", ()) or ()),
        "rework_categories": list(getattr(plan, "failure_categories", ()) or ()),
        "rework_summary": dict(getattr(plan, "rework_summary", {}) or {}),
        "replan_classification": getattr(plan, "classification", ""),
    })
    return ZfEvent(
        type=trigger,
        actor="zf-cli",
        payload=payload,
        correlation_id=str(getattr(plan, "trace_id", "") or ""),
    )


def _latest_trigger_payload(
    events: Sequence[ZfEvent],
    *,
    trigger: str,
    trace_id: str,
) -> dict:
    payload: dict = {}
    for event in events:
        if event.type != trigger:
            continue
        candidate = event.payload if isinstance(event.payload, dict) else {}
        event_trace = str(candidate.get("trace_id") or event.correlation_id or "")
        if trace_id and event_trace and event_trace != trace_id:
            continue
        payload = dict(candidate)
    return payload
