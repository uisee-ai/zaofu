"""Recognize a Plan batch that failed before any Reader call could be issued."""

from zf.core.events import ZfEvent
from zf.runtime.failure_kind import FAILURE_KIND_INFRA, failure_kind_from_payload


def _same_run(event: ZfEvent, run: str) -> bool:
    ids = [v for v in (event.payload.get("workflow_run_id"), event.payload.get("trace_id"),
                       event.correlation_id) if v]
    return bool(ids) and all(v == run for v in ids)


def preregistration_failures(
    events: list[ZfEvent], *, fanout: str, stage: str, run: str, trigger: str, historical_preflight: str = "",
) -> list[ZfEvent]:
    if not all((fanout, stage, run, trigger)):
        return []
    starts = [e for e in events if e.type == "fanout.started" and e.payload.get("fanout_id") == fanout]
    if len(starts) != 1:
        return []
    start = starts[0]
    body = start.payload
    if (start.origin != "kernel" or not _same_run(start, run) or body.get("stage_id") != stage
            or body.get("topology") != "fanout_reader" or start.causation_id != trigger
            or body.get("trigger_event_id") != trigger
            or not any(e.id == trigger and _same_run(e, run) for e in events[:events.index(start)])):
        return []
    aggregate = body.get("aggregate")
    if not isinstance(aggregate, dict):
        return []
    output_events = [aggregate.get(k) for k in ("child_success_event", "child_failure_event", "success_event")]
    expected = body.get("expected_children")
    if not isinstance(expected, list) or not expected or not all(
        isinstance(c, dict) and isinstance(c.get("child_id"), str) and c["child_id"]
        and isinstance(c.get("role_instance"), str) and c["role_instance"] for c in expected
    ):
        return []
    roster = {c["child_id"]: c["role_instance"] for c in expected}
    if len(roster) != len(expected):
        return []
    attempt_ids = [f"run-{fanout}-{child}" for child in roster]
    failed: dict[str, ZfEvent] = {}
    started = False
    for event in events:
        p = event.payload
        if event.type == "event.malformed" and p.get("ledger_integrity") == "unknown":
            return []
        started = started or event.id == start.id
        in_fanout = p.get("fanout_id") == fanout
        if in_fanout and not started:
            return []
        if event.type.startswith(("workflow.operation.", "workflow.call.")) and (
            in_fanout or event.causation_id == start.id
            or (started and p.get("parent_stage_id") == stage and p.get("workflow_run_id") == run)
            or p.get("active_attempt_id") in attempt_ids or p.get("attempt_id") in attempt_ids
        ):
            return []
        if not in_fanout:
            continue
        if (any(p.get(k) for k in ("operation_id", "control_result_ref", "admitted_call_result_ref",
                                    "call_result_ref", "admitted_control_result_ref", "semantic_verdict"))
                or event.type == "fanout.synth.dispatched"):
            return []
        if (event.type in output_events
                or (event.type == "fanout.synth.completed" and event.id != historical_preflight)
                or (event.type == "fanout.aggregate.completed" and
                    (not historical_preflight or event.causation_id != historical_preflight))):
            return []
        if not event.type.startswith("fanout.child."):
            continue
        child = p.get("child_id")
        if (event.type != "fanout.child.failed" or event.origin != "kernel" or event.causation_id != start.id
                or p.get("stage_id") != stage or not _same_run(event, run) or not isinstance(child, str) or child not in roster
                or child in failed or p.get("role_instance") != roster[child]
                or p.get("run_id") != f"run-{fanout}-{child}" or p.get("report")
                or p.get("failure_scope") != "operation_preregistration"
                or p.get("failure_kind") != FAILURE_KIND_INFRA or p.get("failure_class") != "reader_execution_failure"):
            return []
        failed[child] = event
    return list(failed.values()) if failed.keys() == roster.keys() else []


def plan_failure_kind(events: list[ZfEvent], failure: ZfEvent) -> str:
    """Preserve explicit kinds and recover only the historical preflight misclassification."""
    p = failure.payload
    kind = failure_kind_from_payload(p)
    if (kind or failure.origin != "kernel" or p.get("failure_class") != "plan_candidate_preflight"
            or p.get("status") != "failed" or not p.get("fanout_id")):
        return kind
    try:
        history = events[:events.index(failure) + 1]
    except ValueError:
        return ""
    synth = next((e for e in history if e.id == failure.causation_id), None)
    run = p.get("workflow_run_id") or failure.correlation_id
    if (not synth or synth.origin != "kernel" or synth.type != "fanout.synth.completed"
            or not _same_run(failure, run) or not _same_run(synth, run)
            or any(synth.payload.get(k) != p.get(k) for k in ("fanout_id", "stage_id", "failure_class", "status"))
            or not synth.causation_id):
        return ""
    preflight = p.get("plan_candidate_preflight_ref")
    if (not isinstance(preflight, dict) or preflight != synth.payload.get("plan_candidate_preflight_ref")
            or preflight.get("kind") != "plan_candidate_preflight" or not preflight.get("sha256")
            or not preflight.get("ref") or preflight.get("source_event_id") != synth.causation_id):
        return ""
    started = next((e for e in history if e.type == "fanout.started"
                    and e.payload.get("fanout_id") == p["fanout_id"]), None)
    aggregate = started.payload.get("aggregate") if started else None
    if not isinstance(aggregate, dict) or aggregate.get("failure_event") != failure.type:
        return ""
    rows = preregistration_failures(history, fanout=p["fanout_id"], stage=p.get("stage_id"),
                                    run=run, trigger=synth.causation_id, historical_preflight=synth.id)
    return FAILURE_KIND_INFRA if rows else ""
