"""Preserve admitted Reader recovery instead of retrying a generic candidate."""
from zf.core.events import ZfEvent


def admitted_reader_recovery_owns_failure(events: list[ZfEvent], aggregate: ZfEvent) -> bool:
    """An exact kernel-admitted abstention already has a Reader recovery path."""
    prior = events[:events.index(aggregate)]
    by_id = {event.id: event for event in prior}
    fanout_id = aggregate.payload.get("fanout_id")
    run_id = aggregate.payload.get("workflow_run_id") or aggregate.correlation_id
    if not fanout_id or not run_id:
        return False
    for child in prior:
        body = child.payload
        if (not child.type.endswith(".child.failed")
                or body.get("fanout_id") != fanout_id
                or body.get("output_profile_id") != "candidate-verify"):
            continue
        admission = by_id.get(body.get("semantic_submit_admission_event_id"))
        if (admission is None or admission.type != "workflow.call.result.admitted"
                or admission.origin != "kernel"):
            continue
        admitted = admission.payload
        if (admitted.get("control_result_schema") == "verification-result.v1"
                and admitted.get("semantic_verdict") == "abstained"
                and admitted.get("source_event_id") == child.id
                and admitted.get("workflow_run_id") == run_id
                and body.get("control_result_ref")
                and all(body.get(key) == admitted.get(key) for key in (
                    "operation_id", "request_hash", "workflow_run_id", "control_result_ref"
                ))):
            return True
    return False
