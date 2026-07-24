from __future__ import annotations

import copy

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.goal_completion_receipt import (
    GoalCompletionReceiptError,
    build_goal_completion_receipt,
)


RUN_ID = "run-receipt"
GOAL_ID = "GOAL-RECEIPT"
TARGET = "a" * 40


def completion_events() -> list[ZfEvent]:
    return [
        ZfEvent(
            id="evt-start",
            type="run.goal.started",
            correlation_id=RUN_ID,
            payload={
                "run_id": RUN_ID,
                "goal_id": GOAL_ID,
                "objective": "ship product",
            },
        ),
        ZfEvent(
            id="evt-closure",
            type="goal.closure.synthesized",
            correlation_id=RUN_ID,
            payload={
                "workflow_run_id": RUN_ID,
                "goal_id": GOAL_ID,
                "admitted_call_result_ref": {
                    "ref": "artifacts/closure/result.json",
                    "sha256": "d" * 64,
                },
            },
        ),
        ZfEvent(
            id="evt-claim",
            type="run.goal.completion.claimed",
            correlation_id=RUN_ID,
            payload={
                "run_id": RUN_ID,
                "goal_id": GOAL_ID,
                "claim_id": "claim-1",
                "claim_type": "admitted_goal_closure_result",
                "task_map_generation": "generation-2",
                "target_commit": TARGET,
            },
        ),
        ZfEvent(
            id="evt-candidate",
            type="candidate.ready",
            correlation_id=RUN_ID,
            payload={
                "workflow_run_id": RUN_ID,
                "candidate_ref": f"candidate/{GOAL_ID}",
                "candidate_head_commit": TARGET,
            },
        ),
        ZfEvent(
            id="evt-verify",
            type="fanout.child.completed",
            correlation_id=RUN_ID,
            payload={
                "workflow_run_id": RUN_ID,
                "control_result_schema": "verification-result.v1",
                "admitted_call_result_ref": {
                    "ref": "artifacts/verify/result.json",
                    "sha256": "b" * 64,
                },
            },
        ),
        ZfEvent(
            id="evt-completed",
            type="run.goal.completed",
            actor="zf-cli",
            causation_id="evt-claim",
            correlation_id=RUN_ID,
            payload={
                "run_id": RUN_ID,
                "workflow_run_id": RUN_ID,
                "goal_id": GOAL_ID,
                "feature_id": GOAL_ID,
                "claim_id": "claim-1",
                "claim_event_id": "evt-claim",
                "source_event_id": "evt-closure",
                "task_map_generation": "generation-2",
                "target_commit": TARGET,
                "verified_target_commit": TARGET,
                "verification_event_id": "evt-verify",
                "verification_admitted_call_result_ref": {
                    "ref": "artifacts/verify/result.json",
                    "sha256": "b" * 64,
                },
                "candidate_event_id": "evt-candidate",
                "candidate_ref": f"candidate/{GOAL_ID}",
                "candidate_base_commit": "0" * 40,
                "candidate_head_commit": TARGET,
                "completed_task_ids": ["TASK-1"],
                "task_map_ref": "artifacts/plan/task-map.json",
                "source_index_ref": "artifacts/impl/source-index.json",
                "diff_ref": "artifacts/impl/diff.patch",
                "goal_claim_set_ref": "artifacts/claims.json",
                "goal_claim_set_digest": "c" * 64,
                "admitted_call_result_ref": {
                    "ref": "artifacts/closure/result.json",
                    "sha256": "d" * 64,
                },
                "delivery_policy": "report_only",
                "delivery_status": "not_required",
                "delivery_event_id": "",
            },
        ),
    ]


def test_build_goal_completion_receipt_binds_exact_terminal_and_evidence() -> None:
    events = completion_events()

    receipt = build_goal_completion_receipt(
        events,
        run_id=RUN_ID,
        generated_at="2026-07-24T08:30:00+00:00",
        project_id="zaofu",
    )
    rebuilt = build_goal_completion_receipt(
        events,
        run_id=RUN_ID,
        generated_at="2026-07-24T08:31:00+00:00",
        project_id="zaofu",
    )

    assert receipt["schema_version"] == "goal-completion-receipt.v1"
    assert receipt["is_derived_projection"] is True
    assert receipt["workflow_run_id"] == RUN_ID
    assert receipt["terminal"] == {
        "status": "completed",
        "event_id": "evt-completed",
        "event_type": "run.goal.completed",
        "event_seq": 5,
        "event_ts": events[-1].ts,
        "event_sha256": receipt["terminal"]["event_sha256"],
        "claim_id": "claim-1",
    }
    assert receipt["completion_gate"]["status"] == "passed"
    assert receipt["completion_gate"]["target_commit"] == TARGET
    assert receipt["completion_gate"]["verified_target_commit"] == TARGET
    assert receipt["event_refs"]["completion_claim"]["event_id"] == "evt-claim"
    assert receipt["event_refs"]["verification"]["event_id"] == "evt-verify"
    assert receipt["event_refs"]["candidate"]["event_id"] == "evt-candidate"
    assert [item["kind"] for item in receipt["evidence_refs"]] == [
        "goal_claim_set",
        "goal_closure_result",
        "candidate_verification_result",
    ]
    assert receipt["cursor"]["last_event_id"] == "evt-completed"
    assert receipt["degraded"] is False
    assert receipt["source_fingerprint"] == rebuilt["source_fingerprint"]


def test_goal_completion_receipt_fails_closed_without_unique_completion() -> None:
    with pytest.raises(GoalCompletionReceiptError) as unknown:
        build_goal_completion_receipt(
            completion_events(),
            run_id="run-missing",
            generated_at="2026-07-24T08:30:00+00:00",
        )
    assert unknown.value.code == "run_not_found"

    events = completion_events()[:-1]
    with pytest.raises(GoalCompletionReceiptError) as missing:
        build_goal_completion_receipt(
            events,
            run_id=RUN_ID,
            generated_at="2026-07-24T08:30:00+00:00",
        )
    assert missing.value.code == "completion_not_admitted"

    events = completion_events()
    events.append(copy.deepcopy(events[-1]))
    events[-1].id = "evt-completed-duplicate"
    with pytest.raises(GoalCompletionReceiptError) as duplicate:
        build_goal_completion_receipt(
            events,
            run_id=RUN_ID,
            generated_at="2026-07-24T08:30:00+00:00",
        )
    assert duplicate.value.code == "completion_not_unique"


def test_goal_completion_receipt_rejects_incomplete_or_stale_evidence() -> None:
    events = completion_events()
    del events[-1].payload["verification_admitted_call_result_ref"]
    with pytest.raises(GoalCompletionReceiptError) as incomplete:
        build_goal_completion_receipt(
            events,
            run_id=RUN_ID,
            generated_at="2026-07-24T08:30:00+00:00",
        )
    assert incomplete.value.code == "completion_evidence_incomplete"
    assert "missing:verification_admitted_call_result_ref.ref" in (
        incomplete.value.diagnostics
    )

    events = completion_events()
    events.append(ZfEvent(
        id="evt-reactivate",
        type="run.goal.updated",
        correlation_id=RUN_ID,
        payload={"run_id": RUN_ID, "status": "active"},
    ))
    with pytest.raises(GoalCompletionReceiptError) as stale:
        build_goal_completion_receipt(
            events,
            run_id=RUN_ID,
            generated_at="2026-07-24T08:30:00+00:00",
        )
    assert "stale:later_run.goal.updated" in stale.value.diagnostics


def test_goal_completion_receipt_redacts_exported_refs() -> None:
    events = completion_events()
    events[-1].payload["diff_ref"] = "TOKEN=top-secret"

    receipt = build_goal_completion_receipt(
        events,
        run_id=RUN_ID,
        generated_at="2026-07-24T08:30:00+00:00",
    )

    assert receipt["source_refs"]["diff_ref"] == "TOKEN=[REDACTED_SECRET]"
