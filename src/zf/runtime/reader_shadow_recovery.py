"""Keep a proven shadow observation as evidence on an owner-approved retry."""

from typing import Any

from zf.core.events import ZfEvent


def retain_shadow_as_observation(
    trigger: ZfEvent, events: list[ZfEvent], *, approval_ref: str, request_id: str,
) -> None:
    if not approval_ref:
        return
    payload = trigger.payload
    decision = payload.get("orchestrator_decision_ref")
    if not isinstance(decision, dict) or not decision.get("sha256"):
        return
    run_id = str(payload.get("workflow_run_id") or payload.get("trace_id") or trigger.correlation_id or "")
    matching = [e for e in events if e.origin == "kernel"
                and e.payload.get("workflow_run_id") == run_id]
    # An applied semantic return retains its exact baseline and authority.
    if any(e.type == "orchestrator.replan_requested"
           and e.payload.get("orchestrator_decision_ref") == decision for e in matching):
        return
    shadow = next((e for e in reversed(matching)
                   if e.type == "orchestrator.semantic.decision.shadowed"
                   and e.payload.get("decision_ref") == decision
                   and e.payload.get("checkpoint_policy") == "shadow"
                   and e.payload.get("applied") is False), None)
    if shadow is None:
        return
    context: dict[str, Any] = {
        "authority": "observation_only", "shadow_event_id": shadow.id,
        "approval_ref": approval_ref, "authorization_event_id": request_id,
    }
    for key in ("orchestrator_decision_ref", "orchestration_delta",
                "orchestration_delta_ref", "orchestration_delta_digest"):
        if key in payload:
            context[key] = payload.pop(key)
    payload["historical_orchestration_context"] = context
