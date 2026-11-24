"""Recognize a bounded, delivered resident diagnosis while the Goal has no worker operation."""
from datetime import datetime, timezone

from zf.core.config.schema import RuntimeRunManagerResidentAgentConfig

from zf.core.events.model import ZfEvent
from zf.runtime.pause_lifecycle import PAUSE_EVENT_TYPES, RESUME_EVENT_TYPES, active_request_age_seconds
from zf.runtime.recovery_case import build_recovery_case_projection
from zf.runtime.resident_request_obligations import resident_request_obligations


def resident_diagnosis_in_flight(
    events: list[ZfEvent], *, run_id: str, resident: RuntimeRunManagerResidentAgentConfig | None, now: datetime,
) -> bool:
    if not resident or not resident.enabled or not resident.instance_id:
        return False
    owner = resident.instance_id
    case_events = [e for e in events if e.origin == "kernel" and e.payload.get("recovery_case_id")
                   and e.payload.get("workflow_run_id") == run_id]
    if not case_events:
        return False
    verified = {e.id: e.payload for e in case_events if e.actor == "run-manager"
                and e.type == "run.manager.action.verify.passed" and e.payload.get("status") == "passed"}
    cases = build_recovery_case_projection(case_events)["cases"]
    pending_ids = {c["pending_event_id"] for c in cases if c["status"] == "verifying"
                   and c.get("pending_action") == "resident-agent-reprompt"}
    pending: dict[str, ZfEvent] = {}
    for event in case_events:
        if event.id not in pending_ids or event.actor != "run-manager":
            continue
        effect = event.payload
        receipt = verified.get(event.causation_id)
        if (not receipt or event.causation_id != effect.get("receipt_verification_event_id")
                or any(not effect.get(key) or effect[key] != receipt.get(key) for key in (
                    "recovery_case_id", "request_id", "checkpoint_id", "action"))):
            continue
        if (effect.get("status") == "pending" and effect.get("effect_id") and effect.get("instance_id") == owner
                and effect.get("request_kind") == "recovery_diagnosis"
                and "run.manager.agent.recommendation" in (effect.get("expected_event_types") or [])):
            pending[effect["request_id"]] = event
    if not pending:
        return False

    # A resident receipt carries the Run in diagnosis_context (not correlation_id).
    # Reuse its response clock and keep the original deadline across a resend.
    evidence = []
    delivered = set()
    for e in events:
        p = e.payload
        if e.type in PAUSE_EVENT_TYPES | RESUME_EVENT_TYPES:
            evidence.append(e)
        elif e.origin == "kernel" and e.actor == "run-manager-watchdog":
            resident_dead = e.type == "run.manager.unhealthy" and p.get("reason") in {
                "resident_pane_dead", "resident_pane_unhealthy",
            }
            owner_replaced = p.get("instance_id") == owner and e.type in {
                "run.manager.resident.restart_requested", "run.manager.resident.restart_failed",
                "run.manager.resident.restarted",
            }
            if resident_dead or owner_replaced:
                delivered.clear()
        elif (request_id := str(p.get("request_id") or "")) in pending:
            if e.type == "run.manager.resident.prompted" and e.origin == "kernel" and e.actor == "run-manager":
                context = p.get("diagnosis_context") or {}
                if (isinstance(context, dict) and p.get("prompted") is True and p.get("instance_id") == owner
                        and p.get("source_action_checkpoint_id") == pending[request_id].payload["checkpoint_id"]
                        and context.get("workflow_run_id") == run_id and context.get("request_kind") == "recovery_diagnosis"
                        and "run.manager.agent.recommendation" in (p.get("expected_output") or [])):
                    evidence.append(e)
                    delivered.add(request_id)
            elif (e.type == "run.manager.agent.recommendation" and e.actor == owner
                  and p.get("workflow_run_id") == run_id):
                evidence.append(e)

    for obligation in resident_request_obligations(evidence, now=now, timeout_seconds=resident.response_timeout_seconds):
        if obligation["status"] != "pending" or obligation["age_seconds"] is None or obligation["request_id"] not in delivered:
            continue
        effect = pending[obligation["request_id"]]
        timeout = effect.payload.get("effect_timeout_seconds")
        if type(timeout) is not int or timeout <= 0:
            continue
        try:
            created = datetime.fromisoformat(effect.ts)
            created = created.replace(tzinfo=created.tzinfo or timezone.utc)
        except (ValueError, TypeError):
            continue
        if created <= now and active_request_age_seconds(events, created_at=created, now=now) < timeout:
            return True
    return False
