"""Recheck the first missing Candidate quorum rejection for an already signed Judge retry."""

from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import hydrate_call_result_envelope
from zf.runtime.candidate_reader_report import reader_fanout_trigger
from zf.runtime.goal_verification import GOAL_COMPLETION_REVERIFY_CAP, GoalVerificationBinding, bind_canonical_goal_verification
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def recheckable_rejection(event: ZfEvent) -> bool:
    body = event.payload
    return (event.type == "run.goal.completion.rejected" and event.origin == "kernel"
            and body.get("invalid_reasons") == ["verification_evidence_missing"]
            and body.get("completion_recovery_attempt") == 1
            and body.get("completion_recovery_cap") == GOAL_COMPLETION_REVERIFY_CAP)


def previous_quorum_for_claim(
    events: list[ZfEvent], claim: ZfEvent, rejected: ZfEvent, state_dir: Path | None,
) -> GoalVerificationBinding | None:
    """Return the original complete cohort only if that particular rejection was already incorrect."""
    body = claim.payload
    run = body.get("run_id")
    if (not state_dir or claim.origin != "kernel" or not recheckable_rejection(rejected)
            or body.get("claim_type") != "admitted_goal_closure_result"
            or rejected.causation_id != claim.id or rejected.payload.get("claim_event_id") != claim.id
            or claim.causation_id != body.get("source_event_id") or not run):
        return None
    fields = ("run_id", "workflow_run_id", "claim_id", "goal_id", "source_event_id", "target_commit",
              "candidate_ref", "goal_claim_set_digest", "task_map_generation", "admitted_call_result_ref")
    if any(not body.get(k) or rejected.payload.get(k) != body[k] for k in fields):
        return None
    by_id = {e.id: e for e in events}
    if by_id.get(claim.id) != claim or by_id.get(rejected.id) != rejected:
        return None
    judge = by_id.get(claim.causation_id)
    if (not judge or judge.type != "goal.closure.synthesized" or judge.origin != "kernel"
            or judge.payload.get("workflow_run_id") != run or not judge.payload.get("rework_of")
            or judge.payload.get("status") != "completed"):
        return None
    trigger = reader_fanout_trigger(events[:events.index(judge)], by_id, judge.payload.get("fanout_id"))
    failure = by_id.get(judge.payload["rework_of"])
    if (not trigger or trigger.type not in {"flow.goal.closed", "module.parity.closed"} or trigger.origin != "kernel"
            or not failure or failure.type != "goal.closure.synthesis.failed" or failure.origin != "kernel"
            or failure.payload.get("workflow_run_id") != run or trigger.causation_id != failure.id
            or trigger.payload.get("rework_of") != failure.id):
        return None
    admitted = [e for e in events if e.type == "workflow.call.result.admitted" and e.origin == "kernel"
                and e.payload.get("envelope_ref") == body["admitted_call_result_ref"]
                and e.payload.get("control_result_ref") == body.get("control_result_ref")
                and e.payload.get("control_result_schema") == "goal-closure-result.v1"
                and e.payload.get("workflow_run_id") == run and e.payload.get("semantic_verdict") == "passed"
                and e.payload.get("admission_status") == "admitted"]
    if len(admitted) != 1:
        return None
    before = events.index(rejected)
    if not events.index(admitted[0]) < events.index(judge) < events.index(claim) < before:
        return None
    changed = {"goal.closure.synthesized", "goal.closure.superseded", "goal.closure.blocked", "goal.closure.rejected",
               "flow.goal.closed", "module.parity.closed", "goal.claim_set.pinned", "run.goal.updated",
               "plan.approved", "plan.artifact_package.admitted", "task_map.ready", "run.goal.completion.claimed"}
    for later in events[before+1:]:
        if (later.origin == "kernel" and (later.type in changed or
                (later.type == "workflow.call.result.admitted" and later.payload.get("control_result_schema") in
                 {"verification-result.v1", "goal-closure-result.v1"}))):
            return None
    from zf.runtime.run_manager import run_goal_completion_claim_event

    reproduced = run_goal_completion_claim_event(events[:events.index(claim)], cause=judge)
    if not reproduced or reproduced.payload != body:
        return None
    try:
        envelope = hydrate_call_result_envelope(state_dir, body["admitted_call_result_ref"])
        control = hydrate_sidecar_ref(state_dir, body["control_result_ref"]).payload
        source = by_id[admitted[0].payload["source_event_id"]]
        identity = envelope["identity"]
        if (control != judge.payload["goal_closure_result"] or control["verdict"] != "passed"
                or control["recommended_action"] != "complete" or source.id not in envelope["source_event_ids"]
                or source.payload.get("semantic_submit_admission_event_id") != admitted[0].id
                or source.payload.get("call_result_envelope_ref") != admitted[0].payload["envelope_ref"]
                or source.payload.get("control_result_ref") != admitted[0].payload["control_result_ref"]
                or any(envelope["control_result"].get(k) != body["control_result_ref"].get(k) for k in ("ref", "sha256"))
                or any(not body.get(k) or identity.get(k) != body[k] for k in ("workflow_run_id", "target_commit",
                                                                              "task_map_generation", "operation_id"))):
            return None
    except (KeyError, OSError, ValueError, TypeError):
        return None
    prior = bind_canonical_goal_verification(events[:before], claim_payload=body)
    if (prior.status != "exact" or not prior.verification_batch_event_id or not prior.verification_member_event_ids
            or not prior.candidate_event_id or prior.candidate_event_id != rejected.payload.get("candidate_event_id")
            or not prior.candidate_payload.get("freeze_receipt_ref")):
        return None
    # The historical gate stored refs. On this exceptional replay also check
    # that the original freeze and every accepted Reader still hydrate.
    try:
        receipt = hydrate_sidecar_ref(state_dir, prior.candidate_payload["freeze_receipt_ref"]).payload
        if (receipt["schema_version"] != "candidate-freeze-receipt.v1" or receipt["workflow_run_id"] != run
                or receipt["freeze_id"] != prior.candidate_payload["freeze_id"]
                or receipt["candidate_head_commit"] != body["target_commit"]
                or receipt["task_map_generation"] != body["task_map_generation"]):
            return None
        for member in prior.verification_member_event_ids:
            child = by_id[member]
            vote = hydrate_call_result_envelope(state_dir, child.payload["admitted_call_result_ref"])
            report = hydrate_sidecar_ref(state_dir, vote["control_result"]).payload
            if (child.causation_id not in vote["source_event_ids"] or report["verdict"] != "passed"
                    or report["verification_owner"] != "candidate_verify" or report["schema_version"] != "verification-result.v1"
                    or any(report[k] != body[k] for k in ("workflow_run_id", "target_commit", "task_map_generation"))):
                return None
    except (KeyError, OSError, ValueError, TypeError):
        return None
    return prior
