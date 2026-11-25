"""Follow a Goal's successful Candidate and Discovery publications to their admitted inputs."""
from zf.core.events import ZfEvent
from zf.core.events.module_parity import is_module_parity_scan_completed_event
from zf.runtime.candidate_reader_report import reader_fanout_trigger
from zf.runtime.candidate_result_binding import same_task_map_generation
from zf.runtime.goal_candidate_batch import bind_goal_candidate_batch
from zf.runtime.goal_verification import current_goal_candidate


class JudgeAdmissionError(ValueError):
    """A declared batch cannot be bound to this closure's evidence."""


def current_judge_admissions(events: list[ZfEvent], source: ZfEvent, *, run: str,
                             candidate_ref: str, target: str, generation: str) -> dict | None:
    scoped = [e for e in events if (e.payload.get("workflow_run_id") or e.correlation_id) == run]
    by_id = {e.id: e for e in scoped}
    candidate = current_goal_candidate(scoped, candidate_ref=candidate_ref, target_commit=target)
    batch = bind_goal_candidate_batch(scoped, candidate, {
        "candidate_ref": candidate_ref, "target_commit": target, "task_map_generation": generation,
    }) if candidate else None
    if batch is None:
        # A missing declaration cannot turn signed batch Reader votes into legacy independent Verify.
        for e in scoped:
            if e.type != "workflow.call.result.admitted" or e.origin != "kernel":
                continue
            vote = by_id.get(e.payload.get("source_event_id"))
            if (vote and vote.type in {"verify.child.completed", "verify.child.failed"}
                    and vote.payload.get("output_profile_id") == "candidate-verify"
                    and vote.payload.get("semantic_submit_admission_event_id") == e.id
                    and vote.payload.get("target_commit") == target
                    and (vote.payload.get("candidate_ref") or vote.payload.get("target_ref")) == candidate_ref
                    and same_task_map_generation(vote.payload.get("task_map_generation") or "", generation)):
                raise JudgeAdmissionError("signed Candidate Reader votes lack their declared batch")
        return None
    if batch.status != "exact" or source.origin != "kernel" or source.id not in by_id:
        raise JudgeAdmissionError("current Candidate Reader batch is incomplete or ambiguous")
    readers = [
        by_id[by_id[child].causation_id].payload["semantic_submit_admission_event_id"]
        for child in batch.verification_member_event_ids
    ]
    discovery = []
    if source.id != batch.verification_batch_event_id:
        request_type = "flow.discovery.requested" if source.type == "flow.discovery.completed" else ""
        if is_module_parity_scan_completed_event(source.type):
            request_type = "verify.parity_scan.requested"
        fanout = source.payload.get("fanout_id")
        request = reader_fanout_trigger(scoped, by_id, fanout)
        if (not request_type or not request or request.origin != "kernel" or request.type != request_type
                or (request.payload.get("verification_event_id") or request.causation_id) != batch.verification_batch_event_id):
            raise JudgeAdmissionError("Discovery does not follow the current Candidate publication")
        discovery = _discovery_admissions(scoped, by_id, source, request, fanout)
    explicit_refs = {ref for key in ("artifact_refs", "evidence_refs", "regression_refs", "test_refs", "e2e_refs")
                     for ref in source.payload.get(key) or [] if isinstance(ref, str)}
    supporting = [e.id for e in scoped if e.origin == "kernel" and e.type == "workflow.call.result.admitted"
                  and any((e.payload.get(k) or {}).get("ref") in explicit_refs
                          for k in ("control_result_ref", "envelope_ref"))]
    return {"candidate_event_id": batch.candidate_event_id, "publication": batch.verification_batch_event_id,
            "readers": readers, "discovery": discovery, "supporting": supporting}


def _discovery_admissions(events: list[ZfEvent], by_id: dict[str, ZfEvent], source: ZfEvent,
                          request: ZfEvent, fanout: str) -> list[str]:
    aggregate = by_id.get(source.causation_id)
    if (not aggregate or aggregate.origin != "kernel" or aggregate.type != "fanout.aggregate.completed"
            or aggregate.payload.get("fanout_id") != fanout or aggregate.payload.get("status") != "completed"
            or aggregate.payload.get("success_event") != source.type
            or events.index(aggregate) >= events.index(source)):
        raise JudgeAdmissionError("Discovery publication lacks its completed aggregate")
    prior = [e for e in events[:events.index(aggregate)] if e.origin == "kernel" and e.payload.get("fanout_id") == fanout]
    starts = [e for e in prior if e.type == "fanout.started" and e.payload.get("trigger_event_id") == request.id]
    if len(starts) != 1:
        raise JudgeAdmissionError("Discovery roster is missing or ambiguous")
    start = starts[0]
    children = start.payload.get("expected_children") or []
    roster = {c["child_id"]: c.get("role_instance") for c in children if isinstance(c, dict) and c.get("child_id")}
    terminals = {e.payload.get("child_id"): e for e in prior[prior.index(start)+1:]
                 if e.type in {"fanout.child.dispatched", "fanout.child.completed", "fanout.child.failed"}}
    if not roster or len(roster) != len(children) or set(terminals) != set(roster) or not all(roster.values()):
        raise JudgeAdmissionError("Discovery is missing a declared child")
    ids = []
    for child, terminal in terminals.items():
        vote = by_id.get(terminal.causation_id)
        admission = by_id.get(vote.payload.get("semantic_submit_admission_event_id")) if vote else None
        if (terminal.type != "fanout.child.completed" or terminal.payload.get("role_instance") != roster[child]
                or not vote or vote.type != (start.payload.get("aggregate") or {}).get("child_success_event")
                or vote.payload.get("role_instance") != roster[child]
                or vote.payload.get("operation_id") != terminal.payload.get("operation_id")
                or vote.payload.get("fanout_id") != fanout or vote.payload.get("child_id") != child
                or not admission or admission.type != "workflow.call.result.admitted" or admission.origin != "kernel"
                or admission.payload.get("source_event_id") != vote.id or not admission.payload.get("envelope_ref")
                or terminal.payload.get("admitted_call_result_ref") != admission.payload["envelope_ref"]
                or not terminal.payload.get("operation_id")
                or terminal.payload["operation_id"] != admission.payload.get("operation_id")):
            raise JudgeAdmissionError("Discovery child lacks its admitted result")
        ids.append(admission.id)
    return ids
