"""A refused recovery registration cannot replace an already verified native Candidate."""

import pytest

from tests.test_goal_candidate_batch import _retried_judge, event
from zf.runtime.goal_verification import bind_canonical_goal_verification


def _refused_completion_reverification():
    rows, claim = _retried_judge()
    native = rows[0]
    native.payload.update(
        schema_version="candidate-freeze-receipt.v1",
        freeze_receipt_ref="immutable",
        freeze_id="original-freeze",
        candidate_base_commit="base",
    )
    rows[1].payload["stage_id"] = "candidate-review"
    rows[-1].payload["goal_closure_result"] = {"verdict": "passed"}
    claim["claim_id"] = "claim-7"
    receipt = event("run.goal.completion.claimed", "claim-event", **claim)
    rejected = event(
        "run.goal.completion.rejected",
        "rejection",
        cause=receipt.id,
        **claim,
        invalid_reasons=["verification_evidence_missing"],
        candidate_event_id=native.id,
    )
    scope = {
        k: native.payload[k]
        for k in ("freeze_id", "candidate_ref", "candidate_base_commit", "candidate_head_commit")
    }
    signal = event(
        "candidate.ready",
        "unregistered",
        cause=rejected.id,
        **scope,
        rework_of=rejected.id,
        rework_source=rejected.type,
        source="workflow_resume_batch",
        resume_checkpoint_ref="checkpoint",
        idempotency_key="checkpoint",
        candidate_snapshot_event_id="",
    )
    applied = event(
        "workflow.resume.applied",
        "applied",
        cause=rejected.id,
        **scope,
        source_event_id=rejected.id,
        emitted_event_id=signal.id,
        safe_resume_action="reemit_candidate_ready",
        checkpoint_id="checkpoint",
    )
    retry = event(
        signal.type,
        "stall-copy",
        **{
            **signal.payload,
            "original_trigger_event_id": signal.id,
            "redispatch_fingerprint": "logged",
            "redispatch_attempt": 1,
        },
    )
    retry.actor = "zf-stall-redispatch"

    def refusal(e):
        return event(
            "task.fanout.rejected",
            "refused-" + e.id,
            cause=e.id,
            source_event_id=e.id,
            reason="invalid_candidate_reader_assignment",
            stage_id="candidate-review",
        )

    return rows + [receipt, rejected, signal, applied, refusal(signal), retry, refusal(retry)], claim


def test_proven_registration_refusals_preserve_an_already_verified_frozen_candidate():
    rows, claim = _refused_completion_reverification()
    result = bind_canonical_goal_verification(rows, claim_payload=claim)
    assert (result.status, result.candidate_event_id, result.verification_batch_event_id) == (
        "exact",
        "current",
        "pub-current",
    )


@pytest.mark.parametrize(
    "change",
    [
        "pending",
        "new_freeze",
        "new_reader",
        "new_operation",
        "new_verdict",
        "wrong_claim",
        "other_failure",
        "missing_resume",
        "fake_copy",
        "bad_cohort",
        "changed_plan",
    ],
)
def test_an_unproven_or_active_reverification_cannot_reuse_old_reader_results(change):
    rows, claim = _refused_completion_reverification()
    by_id = {e.id: e for e in rows}
    if change == "pending":
        rows.pop()
    elif change == "new_freeze":
        rows.append(
            event("candidate.ready", "new", **{**rows[0].payload, "candidate_head_commit": "different"})
        )
    elif change == "new_reader":
        rows.append(
            event(
                "fanout.started", "working", trigger_event_id="unregistered", stage_id="candidate-review"
            )
        )
    elif change == "new_operation":
        rows.append(event("workflow.operation.requested", "new-wrc", parent_stage_id="candidate-review"))
    elif change == "new_verdict":
        rows.append(event("verify.failed", "new-failure"))
    elif change == "wrong_claim":
        by_id["rejection"].payload["claim_id"] = "different"
    elif change == "other_failure":
        by_id["rejection"].payload["invalid_reasons"].append("verification_target_mismatch")
    elif change == "missing_resume":
        rows.remove(by_id["applied"])
    elif change == "fake_copy":
        by_id["stall-copy"].payload["candidate_base_commit"] = "different"
    elif change == "changed_plan":
        rows.append(event("task_map.ready", "new-plan"))
    else:
        by_id["pub-current"].payload["status"] = "failed"
    assert bind_canonical_goal_verification(rows, claim_payload=claim).status == "missing"
