"""Identify exact Candidate Reader results and retain their recovery ownership."""
from zf.core.events import ZfEvent


def _admitted_verdict(source: ZfEvent, by_id: dict[str, ZfEvent], run_id: str) -> str:
    body = source.payload
    admission = by_id.get(body.get("semantic_submit_admission_event_id"))
    if (body.get("output_profile_id") != "candidate-verify" or admission is None
            or admission.type != "workflow.call.result.admitted" or admission.origin != "kernel"):
        return ""
    admitted = admission.payload
    identity = ("operation_id", "request_hash", "workflow_run_id", "control_result_ref")
    if (admitted.get("control_result_schema") == "verification-result.v1"
            and admitted.get("source_event_id") == source.id and body.get("workflow_run_id") == run_id
            and admitted.get("attempt_id") == body.get("attempt_id")
            and all(body.get(key) and body[key] == admitted.get(key) for key in identity)):
        return str(admitted.get("semantic_verdict") or "")
    return ""


def admitted_reader_recovery_owns_failure(events: list[ZfEvent], aggregate: ZfEvent) -> bool:
    """An admitted abstention has a Reader recovery path; avoid restarting the whole batch."""
    prior = events[:events.index(aggregate)]
    by_id = {event.id: event for event in prior}
    fanout_id = aggregate.payload.get("fanout_id")
    run_id = aggregate.payload.get("workflow_run_id") or aggregate.correlation_id
    if not fanout_id or not run_id:
        return False
    return any(child.type.endswith(".child.failed") and child.payload.get("fanout_id") == fanout_id
               and _admitted_verdict(child, by_id, run_id) == "abstained" for child in prior)


def candidate_reader_outcomes(
    events: list[ZfEvent], terminals: dict[str, str], fanout_id: str, run_id: str,
) -> dict[str, dict[str, str]]:
    """Summarize the kernel's terminal facts without inheriting an older attempt's verdict."""
    by_id = {e.id: e for e in events}
    groups: dict[str, dict[str, str]] = {}
    for child_id, terminal_id in terminals.items():
        event = by_id.get(terminal_id)
        if (not event or event.origin != "kernel"
                or event.type not in {"fanout.child.failed", "fanout.child.completed"}
                or event.payload.get("child_id") != child_id or event.payload.get("fanout_id") != fanout_id):
            continue
        payload = event.payload
        source = by_id.get(event.causation_id)
        outcome = "unconfirmed"
        if (source and source.payload.get("fanout_id") == fanout_id and source.payload.get("child_id") == child_id
                and all(payload.get(k) and payload[k] == source.payload.get(k)
                        for k in ("operation_id", "request_hash", "workflow_run_id", "control_result_ref", "attempt_id"))):
            verdict = _admitted_verdict(source, by_id, run_id)
            if verdict in {"passed", "rejected", "abstained", "blocked"} and payload.get("semantic_verdict") == verdict:
                outcome = verdict
        if (event.type == "fanout.child.failed" and not payload.get("control_result_ref")
                and payload.get("failure_class") == "worker_noop_or_terminal_missing"):
            outcome = "without_result"
        groups.setdefault(outcome, {})[child_id] = event.id
    return groups
