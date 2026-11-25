"""Bind a Goal to one fully admitted batch of Candidate Readers."""

from typing import Any, Mapping, Sequence

from zf.core.events import ZfEvent
from zf.core.events.module_parity import is_module_parity_scan_completed_event
from zf.runtime.candidate_reader_recovery_owner import candidate_reader_outcomes
from zf.runtime.candidate_reader_report import reader_fanout_trigger
from zf.runtime.candidate_result_binding import same_task_map_generation
from zf.runtime.goal_verification import GoalVerificationBinding


def bind_goal_candidate_batch(
    events: Sequence[ZfEvent],
    candidate: ZfEvent,
    claim: Mapping[str, Any],
) -> GoalVerificationBinding | None:
    """Return None for flows without a declared Candidate verification batch."""
    by_id = {e.id: e for e in events}
    run = candidate.payload.get("workflow_run_id") or candidate.correlation_id
    declarations = []
    for e in events:
        if e.type != "fanout.started" or e.origin != "kernel":
            continue
        body = e.payload
        config = body.get("aggregate") or {}
        parent = by_id.get(body.get("trigger_event_id"))
        if (
            parent
            and parent.type == "candidate.ready"
            and parent.origin == "kernel"
            and run
            and (body.get("workflow_run_id") or e.correlation_id) == run
            and body.get("topology") == "fanout_reader"
            and config.get("mode") == "wait_for_all"
            and config.get("child_success_event") == "verify.child.completed"
            and config.get("success_event") in {"test.passed", "verify.passed"}
        ):
            declarations.append(e)
    if not declarations:
        return None
    declared_ids = {d.payload.get("fanout_id") for d in declarations}
    if not candidate.payload.get("freeze_receipt_ref") and not any(
        e.type in {"verify.child.completed", "verify.child.failed"}
        and e.payload.get("output_profile_id") == "candidate-verify"
        and e.payload.get("fanout_id") in declared_ids
        for e in events
    ):
        return None  # Legacy readers use the independent verification-result contract.

    missing = GoalVerificationBinding(
        status="missing",
        invalid_reason="verification_evidence_missing",
        candidate_event_id=candidate.id,
        candidate_payload=dict(candidate.payload),
    )
    starts = [e for e in declarations if e.payload["trigger_event_id"] == candidate.id]
    if len(starts) != 1:
        return missing
    verified_candidate = candidate
    if candidate.payload.get("freeze_receipt_ref"):
        from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload

        try:
            pinned = frozen_candidate_for_payload(
                list(events), run, payload={"candidate_event_id": candidate.id}
            )
            latest = frozen_candidate_for_payload(list(events), run, payload={})
        except ValueError:
            return missing
        if not pinned or not latest or pinned.id != latest.id:
            return missing
        verified_candidate = pinned
    start = starts[0]
    fanout = start.payload.get("fanout_id")
    scoped = [e for e in events[events.index(start) + 1 :] if e.payload.get("fanout_id") == fanout]
    terminal = next(
        (
            e
            for e in reversed(scoped)
            if e.origin == "kernel"
            and e.type
            in {
                "fanout.aggregate.completed",
                "fanout.aggregate.started",
                "fanout.cancelled",
                "fanout.timed_out",
            }
        ),
        None,
    )
    success = start.payload["aggregate"]["success_event"]
    if (
        not terminal
        or terminal.type != "fanout.aggregate.completed"
        or terminal.payload.get("status") != "completed"
        or terminal.payload.get("success_event") != success
    ):
        return missing
    publications = [
        e for e in scoped if e.type == success and e.origin == "kernel" and e.causation_id == terminal.id
    ]
    if len(publications) != 1:
        return missing
    publication = publications[0]
    if claim.get("source_event_id"):
        judge = by_id.get(claim["source_event_id"])
        if (
            not judge
            or judge.type != "goal.closure.synthesized"
            or judge.origin != "kernel"
            or events.index(publication) >= events.index(judge)
        ):
            return missing
        closure = reader_fanout_trigger(events, by_id, judge.payload.get("fanout_id"))
        if (
            not closure
            or closure.origin != "kernel"
            or closure.type not in {"flow.goal.closed", "module.parity.closed"}
        ):
            return missing
        closure_source = by_id.get(closure.causation_id)
        if not closure_source or closure_source.origin != "kernel":
            return missing
        route = ""
        if closure_source.type == "flow.discovery.completed":
            route = "flow.discovery.requested"
        elif is_module_parity_scan_completed_event(closure_source.type):
            route = "verify.parity_scan.requested"
        if route:
            discovery = reader_fanout_trigger(events, by_id, closure_source.payload.get("fanout_id"))
            if (
                not discovery
                or discovery.type != route
                or discovery.origin != "kernel"
                or (discovery.payload.get("verification_event_id") or discovery.causation_id)
                != publication.id
            ):
                return missing
        elif closure_source != publication:
            return missing
    package = verified_candidate.payload.get("plan_artifact_package_digest")
    target = claim.get("target_commit") or ""
    generation = claim.get("task_map_generation") or ""
    declared = start.payload.get("expected_children") or []
    if not all(isinstance(child, dict) for child in declared):
        return missing
    roster = {child.get("child_id"): child.get("role_instance") for child in declared}
    if (
        not fanout
        or not roster
        or not all(roster)
        or not all(roster.values())
        or len(roster) != len(declared)
        or len(set(roster.values())) != len(roster)
        or publication.payload.get("status") != "completed"
        or publication.payload.get("child_count") != len(roster)
        or publication.payload.get("workflow_run_id") != run
        or (package and publication.payload.get("plan_artifact_package_digest") != package)
        or (publication.payload.get("candidate_ref") or publication.payload.get("target_ref"))
        != claim.get("candidate_ref")
        or (publication.payload.get("candidate_head_commit") or publication.payload.get("target_commit"))
        != target
        or not same_task_map_generation(publication.payload.get("task_map_generation") or "", generation)
    ):
        return missing

    # Check the latest action of each Reader before the aggregate. A later dispatch supersedes an old vote.
    children = {}
    for e in scoped[: scoped.index(terminal)]:
        if e.origin == "kernel" and e.type in {
            "fanout.child.completed",
            "fanout.child.failed",
            "fanout.child.dispatched",
        }:
            children[e.payload.get("child_id")] = e
    if set(children) != set(roster):
        return missing
    terminals = {role: e.id for role, e in children.items()}
    if candidate_reader_outcomes(events[: events.index(terminal)], terminals, fanout, run) != {
        "passed": terminals
    }:
        return missing
    for role, child in children.items():
        b = child.payload
        source = by_id[child.causation_id]
        admission = by_id[source.payload["semantic_submit_admission_event_id"]]
        envelope = b.get("admitted_call_result_ref") or {}
        if (
            child.type != "fanout.child.completed"
            or b.get("role_instance") != roster[role]
            or source.payload.get("role_instance") != roster[role]
            or (package and b.get("plan_artifact_package_digest") != package)
            or not envelope.get("ref")
            or not envelope.get("sha256")
            or envelope != admission.payload.get("envelope_ref")
            or b.get("target_commit") != target
            or (b.get("candidate_ref") or b.get("target_ref")) != claim.get("candidate_ref")
            or not same_task_map_generation(b.get("task_map_generation") or "", generation)
        ):
            return missing
    # Retain the existing signed-child evidence port and also identify the batch that proves completeness.
    representative = children[next(iter(roster))]
    return GoalVerificationBinding(
        status="exact",
        invalid_reason="",
        verified_target_commit=target,
        verification_event_id=representative.id,
        verification_admitted_ref=representative.payload["admitted_call_result_ref"],
        candidate_event_id=verified_candidate.id,
        candidate_payload=dict(verified_candidate.payload),
        verification_batch_event_id=publication.id,
        verification_member_event_ids=tuple(terminals.values()),
    )
