"""Identify registration-only recovery signals from a now-verifiable Judge completion claim."""

from collections.abc import Mapping, Sequence
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.fanout_retrigger_guard import is_stall_trigger_copy


def original_candidate_after_rejected_signal(
    events: Sequence[ZfEvent], candidate: ZfEvent, claim: Mapping[str, Any]
) -> ZfEvent | None:
    """A rejected re-verification request conveys no new freeze or verification decision."""
    from zf.runtime.goal_candidate_batch import bind_goal_candidate_batch

    run = claim.get("workflow_run_id") or claim.get("run_id")
    if not run or not claim.get("claim_id") or not claim.get("source_event_id"):
        return None
    if candidate.payload.get("rework_source") != "run.goal.completion.rejected":
        return None
    scoped = [e for e in events if (e.payload.get("workflow_run_id") or e.correlation_id) == run]
    by_id = {e.id: e for e in scoped}
    rejected = by_id.get(candidate.payload.get("rework_of"))
    if (
        not rejected
        or rejected.type != "run.goal.completion.rejected"
        or rejected.origin != "kernel"
        or rejected.payload.get("invalid_reasons") != ["verification_evidence_missing"]
        or rejected.payload.get("claim_id") != claim["claim_id"]
        or rejected.payload.get("source_event_id") != claim["source_event_id"]
    ):
        return None
    original_claim = by_id.get(rejected.causation_id)
    judge = by_id.get(claim["source_event_id"])
    native = by_id.get(rejected.payload.get("candidate_event_id"))
    if (
        not original_claim
        or original_claim.type != "run.goal.completion.claimed"
        or original_claim.origin != "kernel"
        or original_claim.payload.get("source_event_id") != claim["source_event_id"]
        or original_claim.payload.get("claim_id") != claim["claim_id"]
        or not judge
        or judge.type != "goal.closure.synthesized"
        or judge.origin != "kernel"
        or (judge.payload.get("goal_closure_result") or {}).get("verdict") != "passed"
        or not native
        or native.origin != "kernel"
        or native.type != "candidate.ready"
        or native.payload.get("schema_version") != "candidate-freeze-receipt.v1"
        or not native.payload.get("freeze_receipt_ref")
    ):
        return None

    idx = scoped.index(rejected)
    after = scoped[idx + 1 :]
    proposals = [e for e in after if e.type == "candidate.ready"]
    stages = {
        e.payload.get("stage_id")
        for e in scoped[:idx]
        if e.type == "fanout.started"
        and e.origin == "kernel"
        and e.payload.get("trigger_event_id") == native.id
    }
    stages.discard(None)
    if not stages:
        return None
    if not _authenticated_signals(after, proposals, candidate, rejected, native):
        return None
    if not _all_registrations_refused(after, proposals, stages):
        return None
    # Independently establish the original rejection's error using the real, fully admitted historical cohort.
    original = bind_goal_candidate_batch(scoped[:idx], native, original_claim.payload)
    return native if original and original.status == "exact" else None


def _authenticated_signals(after, proposals, candidate, rejected, native) -> bool:
    """Prove the single kernel resume and any exact copies issued by stall detection."""
    primary = [e for e in proposals if e.causation_id == rejected.id and e.actor == "zf-cli"]
    if len(primary) != 1 or primary[0] != proposals[0] or candidate not in proposals:
        return False
    signal = primary[0]
    p = signal.payload
    marker = p.get("resume_checkpoint_ref")
    fields = ("freeze_id", "candidate_ref", "candidate_base_commit", "candidate_head_commit")
    if (
        not marker
        or marker != p.get("idempotency_key")
        or p.get("source") != "workflow_resume_batch"
        or p.get("rework_of") != rejected.id
        or p.get("rework_source") != rejected.type
        or p.get("candidate_snapshot_event_id")
        or p.get("freeze_receipt_ref")
        or p.get("schema_version")
        or signal.origin != "kernel"
        or any(not p.get(k) or p[k] != native.payload.get(k) for k in fields)
    ):
        return False
    witness = [
        e
        for e in after
        if e.type == "workflow.resume.applied"
        and e.origin == "kernel"
        and e.causation_id == rejected.id
        and e.payload.get("emitted_event_id") == signal.id
        and e.payload.get("checkpoint_id") == marker
        and e.payload.get("source_event_id") == rejected.id
        and e.payload.get("safe_resume_action") == "reemit_candidate_ready"
        and all(e.payload.get(k) == p[k] for k in fields)
    ]
    if len(witness) != 1 or after.index(witness[0]) <= after.index(signal):
        return False
    if any(e.origin != "kernel" or not is_stall_trigger_copy(e, signal) for e in proposals[1:]):
        return False
    return True


def _all_registrations_refused(after, proposals, stages) -> bool:
    """Require receipt-backed refusals and no new Plan, Reader admission, or product result."""
    # A Reader start/dispatch/result means substantive new work; even a pending attempt keeps the gate closed.
    signal_ids = {e.id for e in proposals}
    for e in after:
        if e.type in {
            "verify.child.completed",
            "verify.child.failed",
            "test.passed",
            "test.failed",
            "verify.passed",
            "verify.failed",
            "task_map.ready",
            "plan.artifact_package.admitted",
        }:
            return False
        if e.type in {"fanout.started", "fanout.child.dispatched", "workflow.operation.requested"} and (
            e.payload.get("trigger_event_id") in signal_ids
            or e.payload.get("stage_id") in stages
            or e.payload.get("parent_stage_id") in stages
        ):
            return False
    for proposal in proposals:
        refusals = [
            e
            for e in after
            if e.type == "task.fanout.rejected"
            and e.origin == "kernel"
            and e.causation_id == proposal.id
            and e.payload.get("source_event_id") == proposal.id
            and e.payload.get("stage_id") in stages
            and e.payload.get("reason") == "invalid_candidate_reader_assignment"
        ]
        if len(refusals) != 1 or after.index(refusals[0]) <= after.index(proposal):
            return False
    return True
