"""Resolve a legacy pre-Impl owner escalation to its admitted OA decision."""

from __future__ import annotations

from zf.core.events.model import ZfEvent


def escalated_pre_impl_submission(events: list[ZfEvent], escalation_id: str) -> ZfEvent:
    """Select the original decision only from the exact historical kernel outcome."""
    index = {event.id: (pos, event) for pos, event in enumerate(events)}
    escalation_pos, escalation = index.get(escalation_id, (-1, None))
    if not escalation or escalation.type != "human.escalate" or escalation.origin != "kernel" or escalation.actor != "zf-cli":
        raise ValueError("expected an existing kernel human.escalate")
    submitted_pos, submitted = index.get(escalation.causation_id, (-1, None))
    if (not submitted or submitted.type != "orchestrator.semantic.decision.submitted"
            or submitted_pos >= escalation_pos):
        raise ValueError("escalation has no original submitted OA decision")

    identity = submitted.payload
    run = identity.get("workflow_run_id")
    operation = identity.get("operation_id")
    source = identity.get("source_event_id")
    ref = escalation.payload.get("orchestrator_decision_ref")
    if (not run or not operation or not source or not isinstance(ref, dict)
            or not ref.get("ref") or not ref.get("sha256")
            or escalation.payload.get("orchestrator_operation_id") != operation
            or escalation.correlation_id != submitted.correlation_id
            or escalation.payload.get("workflow_run_id", run) != run):
        raise ValueError("escalation does not match the original Run, operation or decision ref")

    def bound(event: ZfEvent) -> bool:
        p = event.payload
        return (event.origin == "kernel" and event.actor == "zf-cli" and event.causation_id == submitted.id
                and p.get("decision_event_id") == submitted.id and p.get("workflow_run_id") == run
                and p.get("operation_id") == operation and p.get("source_event_id") == source
                and p.get("checkpoint") == "pre_impl" and p.get("checkpoint_policy") == "blocking"
                and p.get("decision") == "revise" and p.get("decision_ref") == ref)

    if not any(event.type == "orchestrator.semantic.decision.admitted" and bound(event)
               for event in events[submitted_pos + 1:escalation_pos]):
        raise ValueError("escalation has no matching admitted blocking pre_impl revision")
    if not any(event.type == "orchestrator.semantic.decision.applied" and bound(event)
               and event.payload.get("applied") is False and event.payload.get("status") == "escalated"
               for event in events[escalation_pos + 1:]):
        raise ValueError("escalation has no historical unapplied pre_impl outcome")
    return submitted
