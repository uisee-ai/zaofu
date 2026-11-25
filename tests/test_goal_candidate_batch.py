"""The terminal goal gate binds one complete Candidate Verify epoch, including on replay."""

from copy import deepcopy

import pytest

from tests.test_goal_completion_receipt import completion_events, RUN_ID, GOAL_ID, TARGET
from zf.core.events import ZfEvent
from zf.runtime.goal_verification import bind_canonical_goal_verification
from zf.runtime.goal_completion_receipt import build_goal_completion_receipt, GoalCompletionReceiptError
from zf.runtime.run_manager import run_goal_completion_claim_event, run_goal_completion_gate_event

REF = f"candidate/{GOAL_ID}"
GEN = "generation-2"
IDENTITY = dict(workflow_run_id=RUN_ID, candidate_ref=REF, target_commit=TARGET, task_map_generation=GEN)


def event(kind, eid, *, cause=None, **payload):
    return ZfEvent(
        type=kind,
        id=eid,
        actor="zf-cli",
        origin="kernel",
        correlation_id=RUN_ID,
        causation_id=cause,
        payload={"workflow_run_id": RUN_ID, **payload},
    )


def batch(epoch, verdicts, *, complete=True):
    freeze = event(
        "candidate.ready",
        epoch,
        candidate_head_commit=TARGET,
        candidate_ref=REF,
        task_map_generation=GEN,
        completed_task_ids=["TASK-1"],
    )
    roster = [{"child_id": str(n), "role_instance": f"reader-{n}"} for n in range(8)]
    config = dict(
        mode="wait_for_all", success_event="test.passed", child_success_event="verify.child.completed"
    )
    fanout = "batch-" + epoch
    start = event(
        "fanout.started",
        fanout,
        cause=epoch,
        fanout_id=fanout,
        trigger_event_id=epoch,
        topology="fanout_reader",
        aggregate=config,
        expected_children=roster,
    )
    rows = [freeze, start]
    for n, verdict in enumerate(verdicts):
        lane = f"{epoch}-{n}"
        envelope = {"ref": f"e/{lane}", "sha256": "a" * 64}
        control = {"ref": f"c/{lane}", "sha256": "c" * 64}
        source = "source-" + lane
        adm = "admission-" + lane
        status = "completed" if verdict == "passed" else "failed"
        binding = {
            **IDENTITY,
            **roster[n],
            "fanout_id": fanout,
            "operation_id": "op-" + lane,
            "request_hash": "h-" + lane,
            "attempt_id": "att-" + lane,
            "control_result_ref": control,
            "control_result_schema": "verification-result.v1",
        }
        rows.extend(
            [
                event(
                    "verify.child." + status,
                    source,
                    output_profile_id="candidate-verify",
                    semantic_submit_admission_event_id=adm,
                    **binding,
                ),
                event(
                    "workflow.call.result.admitted",
                    adm,
                    source_event_id=source,
                    envelope_ref=envelope,
                    semantic_verdict=verdict,
                    **binding,
                ),
                event(
                    "fanout.child." + status,
                    "child-" + lane,
                    cause=source,
                    semantic_verdict=verdict,
                    admitted_call_result_ref=envelope,
                    **binding,
                ),
            ]
        )
    if complete:
        passed = all(v == "passed" for v in verdicts) and len(verdicts) == 8
        status = "completed" if passed else "failed"
        published = "test.passed" if passed else "test.failed"
        agg = event(
            "fanout.aggregate.completed",
            "agg-" + epoch,
            fanout_id=fanout,
            status=status,
            success_event=published if passed else "",
            failure_event="" if passed else published,
        )
        rows.extend(
            [
                agg,
                event(
                    published,
                    "pub-" + epoch,
                    cause=agg.id,
                    fanout_id=fanout,
                    status=status,
                    child_count=8,
                    **IDENTITY,
                ),
            ]
        )
    return rows


OLD = ["passed"] * 7 + ["rejected"]
NEW = ["passed"] * 8


def judge_inputs(judge, closed, epoch):
    publication = "pub-" + epoch
    request = event("flow.discovery.requested", "discovery-request", cause=publication)
    discovery_start = event(
        "fanout.started", "discovery", fanout_id="discovery", trigger_event_id=request.id
    )
    report = event("flow.discovery.completed", "discovery-report", fanout_id="discovery")
    closed.causation_id = report.id
    judge_start = event("fanout.started", "judge-start", fanout_id="judge-role", trigger_event_id=closed.id)
    judge.payload["fanout_id"] = "judge-role"
    discovery_done = event("fanout.aggregate.completed", "discovery-done", fanout_id="discovery")
    report.causation_id = discovery_done.id
    judge_done = event("fanout.aggregate.completed", "judge-done", fanout_id="judge-role")
    judge.causation_id = judge_done.id
    judge.origin = "kernel"
    return [request, discovery_start, discovery_done, report, closed, judge_start, judge_done, judge]


def test_completion_gate_requires_current_admitted_batch_and_replay_retains_its_votes():
    rows = [event("run.goal.started", "run-start", run_id=RUN_ID, goal_id=GOAL_ID)]
    rows += batch("old", OLD) + batch("new", NEW)
    closed = event(
        "flow.goal.closed",
        "closing",
        goal_id=GOAL_ID,
        candidate_head_commit=TARGET,
        goal_claim_set_digest="2" * 64,
        closure_fact_digest="3" * 64,
        **IDENTITY,
    )
    judge = event(
        "goal.closure.synthesized",
        "judge",
        goal_closure_result={
            **IDENTITY,
            "goal_id": GOAL_ID,
            "goal_claim_set_ref": "claims",
            "goal_claim_set_digest": "2" * 64,
            "closure_fact_ref": "closure",
            "closure_fact_digest": "3" * 64,
            "verdict": "passed",
        },
        admitted_call_result_ref={"ref": "judgment", "sha256": "d" * 64},
    )
    rows.extend(judge_inputs(judge, closed, "new"))
    claim = run_goal_completion_claim_event(rows, cause=judge)
    assert claim is not None
    rows.append(claim)
    outcome = run_goal_completion_gate_event(rows, claim=claim)
    assert outcome and outcome.type == "run.goal.completed", outcome and outcome.payload
    assert outcome.payload["verification_batch_event_id"] == "pub-new"
    assert outcome.payload["verification_event_id"] == "child-new-0"
    assert run_goal_completion_gate_event([*rows, outcome], claim=claim) is None


@pytest.mark.parametrize(
    "fault",
    [
        "no-start",
        "pending",
        "rejected",
        "unsigned",
        "wrong-role",
        "wrong-target",
        "forged-envelope",
        "missing-lane",
        "mixed",
        "old-judge",
        "cancelled",
        "old-publication",
    ],
)
def test_old_or_partial_votes_cannot_authorize_the_new_batch(fault):
    old = batch("old", OLD)
    fresh = batch("new", OLD if fault == "rejected" else NEW, complete=fault != "pending")
    q = dict(IDENTITY)
    if fault == "no-start":
        fresh = fresh[:1]
    elif fault == "pending":
        fresh = fresh[:2]
    elif fault == "unsigned":
        fresh = [e for e in fresh if e.id != "admission-new-7"]
    elif fault == "wrong-role":
        next(e for e in fresh if e.id == "child-new-7").payload["role_instance"] = "reader-2"
    elif fault == "wrong-target":
        fresh[-1].payload["target_commit"] = "changed"
    elif fault == "forged-envelope":
        next(e for e in fresh if e.id == "child-new-7").payload["admitted_call_result_ref"] = {
            "ref": "foreign",
            "sha256": "f" * 64,
        }
    elif fault == "missing-lane":
        fresh = [e for e in fresh if e.id != "child-new-7"]
    elif fault == "mixed":
        next(e for e in fresh if e.id == "child-new-7").causation_id = "source-old-7"
    elif fault == "old-judge":
        q["source_event_id"] = "stale"
        old.append(event("goal.closure.synthesized", "stale"))
    elif fault == "cancelled":
        fresh.append(event("fanout.cancelled", "cancel", fanout_id="batch-new"))
    elif fault == "old-publication":
        fresh[-1].causation_id = "agg-old"
    proof = bind_canonical_goal_verification(old + fresh, claim_payload=q)
    assert (
        proof.status == "missing"
        and not proof.verification_event_id
        and not proof.verification_batch_event_id
    )


def test_receipt_replays_the_historical_cut_and_rejects_wrong_provenance():
    started, judge, claim, _, _, completed = completion_events()
    claim.payload.update(IDENTITY, source_event_id=judge.id)
    new = batch("new", NEW)
    new[0].payload["candidate_base_commit"] = "0" * 40
    completed.payload.update(
        candidate_event_id="new",
        verification_batch_event_id="pub-new",
        verification_event_id="child-new-0",
        verification_admitted_call_result_ref={"ref": "e/new-0", "sha256": "a" * 64},
    )
    closed = event("flow.goal.closed", "closed")
    rows = [started, *batch("old", OLD), *new, *judge_inputs(judge, closed, "new"), claim, completed]
    assert (
        build_goal_completion_receipt(
            rows + batch("future", OLD), run_id=RUN_ID, generated_at="2026-11-25T00:00:00Z"
        )["event_refs"]["candidate_verification_batch"]["event_id"]
        == "pub-new"
    )
    legacy = deepcopy(completed)
    del legacy.payload["verification_batch_event_id"]
    legacy.payload.update(
        verification_event_id="child-new-7",
        verification_admitted_call_result_ref={"ref": "e/new-7", "sha256": "a" * 64},
    )
    build_goal_completion_receipt([*rows[:-1], legacy], run_id=RUN_ID, generated_at="2026-11-25T00:00:00Z")
    for key, val in (
        ("verification_batch_event_id", "pub-old"),
        ("verification_event_id", "child-old-0"),
    ):
        tampered = deepcopy(completed)
        tampered.payload[key] = val
        with pytest.raises(GoalCompletionReceiptError, match="completion evidence is incomplete"):
            build_goal_completion_receipt(
                [*rows[:-1], tampered], run_id=RUN_ID, generated_at="2026-11-25T00:00:00Z"
            )


def test_new_judge_execution_against_old_discovery_cannot_close_the_new_batch():
    rows = batch("old", NEW) + batch("new", NEW)
    judge = event("goal.closure.synthesized", "judge")
    rows.extend(judge_inputs(judge, event("flow.goal.closed", "closing"), "old"))
    proof = bind_canonical_goal_verification(rows, claim_payload={**IDENTITY, "source_event_id": judge.id})
    assert proof.status == "missing"


def test_reverification_uses_the_original_native_freeze_and_the_current_batch():
    old = batch("native", OLD)
    old[0].payload.update(
        schema_version="candidate-freeze-receipt.v1", freeze_id="fixed", freeze_receipt_ref="immutable"
    )
    fresh = batch("reattempt", NEW)
    fresh[0].payload.update(
        candidate_snapshot_event_id="native", freeze_id="fixed", freeze_receipt_ref="immutable"
    )
    proof = bind_canonical_goal_verification(old + fresh, claim_payload=IDENTITY)
    assert (proof.status, proof.candidate_event_id, proof.verification_batch_event_id) == (
        "exact",
        "native",
        "pub-reattempt",
    )
