"""Retire an expired advisory notice only after the same worker hands off."""

from zf.core.events.model import ZfEvent


def assistance_handoff_retired(events: list[ZfEvent], source: ZfEvent) -> bool | None:
    """None preserves other human gates' existing lifecycle policy.

    A late advisor answer cannot close the notice. The original writer must
    have an admitted result, kernel settlement and kernel recovery triage.
    This proves routing ownership changed; it grants no human approval.
    """
    p = source.payload
    if (source.type != "human.escalate" or source.origin != "kernel"
            or source.actor != "run-manager" or p.get("blocking_scope") != "task"
            or p.get("resume_condition") != "current advisor response or authorized handoff"
            or p.get("reason") not in {
                "assistance response deadline expired", "assistance owner unavailable",
                "assistance owner did not deliver the current answer",
            }
            or any(p.get(key) for key in (
                "decision_token", "response_token", "approval_ref", "approval_command",
                "checkpoint_id", "fingerprint", "suggested_options",
            ))):
        return None
    by_id = {e.id: e for e in events}
    order = {e.id: index for index, e in enumerate(events)}
    request = by_id.get(str(source.causation_id or ""))
    identity = _scope(source)
    if (request is None or request.type != "worker.assistance.requested"
            or identity is None or _scope(request) != identity
            or not p.get("request_id") or request.payload.get("request_id") != p["request_id"]
            or request.payload.get("owner") != p.get("owner_route")
            or request.payload.get("apply_policy") != "proposal_only"
            or order[request.id] >= order[source.id]):
        return False
    required = ("operation_id", "active_attempt_id", "lease_id")
    if not all(request.payload.get(key) for key in required):
        return False
    if not any(
        e.type == "worker.assistance.closed" and e.origin == "kernel"
        and e.actor == "run-manager" and e.causation_id == request.id
        and _scope(e) == identity and e.payload.get("request_id") == p["request_id"]
        and e.payload.get("status") == "waiting" and e.payload.get("reason") == p["reason"]
        and all(e.payload.get(key) == request.payload[key] for key in required)
        and order[request.id] < order[e.id] < order[source.id]
        for e in events
    ):
        return False
    for triage in events[order[source.id] + 1:]:
        if (triage.type != "task.rework.triage.completed" or triage.origin != "kernel"
                or _scope(triage) != identity
                or triage.payload.get("recommended_action") != "request_replan"
                or triage.payload.get("is_terminal") is not False):
            continue
        failure = by_id.get(str(triage.payload.get("failed_event_id") or ""))
        if (failure is None or _scope(failure) != identity or failure.actor != request.actor
                or not order[source.id] < order[failure.id] < order[triage.id]
                or failure.payload.get("operation_id") != request.payload["operation_id"]
                or failure.payload.get("attempt_id") != request.payload["active_attempt_id"]
                or failure.payload.get("lease_id") != request.payload["lease_id"]):
            continue
        admission = by_id.get(str(failure.payload.get("semantic_submit_admission_event_id") or ""))
        if (admission is None or admission.type != "workflow.call.result.admitted"
                or admission.origin not in {"", "kernel"} or _scope(admission) != identity
                or admission.payload.get("schema_version") != "call-result-admission.v1"
                or admission.payload.get("admission_status") != "admitted"
                or admission.payload.get("semantic_verdict") not in {"blocked", "failed", "rejected"}
                or admission.payload.get("source_event_id") != failure.id):
            continue
        envelope = failure.payload.get("call_result_envelope_ref")
        request_hash = failure.payload.get("request_hash")
        if (not isinstance(envelope, dict) or not envelope.get("ref") or not envelope.get("sha256")
                or not request_hash or admission.payload.get("envelope_ref") != envelope
                or admission.payload.get("request_hash") != request_hash
                or admission.payload.get("operation_id") != request.payload["operation_id"]):
            continue
        if any(
            e.type == "workflow.operation.settled" and e.origin == "kernel"
            and _scope(e) == identity and e.payload.get("reason") == "admitted_call_result"
            and e.payload.get("operation_id") == request.payload["operation_id"]
            and e.payload.get("request_hash") == request_hash
            and e.payload.get("admitted_call_result_ref") == envelope
            and order[source.id] < order[admission.id] < order[e.id] < order[triage.id]
            for e in events
        ):
            return True
    return False


def _scope(event: ZfEvent) -> tuple[str, str] | None:
    runs = {str(v) for v in (event.correlation_id, event.payload.get("workflow_run_id"),
                            event.payload.get("run_id")) if v}
    tasks = {str(v) for v in (event.task_id, event.payload.get("task_id")) if v}
    return (next(iter(runs)), next(iter(tasks))) if len(runs) == len(tasks) == 1 else None
