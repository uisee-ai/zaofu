"""End-to-end replay of a rejected task ref, independently corrected before candidate freeze."""

import pytest

from zf.core.events import ZfEvent
from zf.runtime.attempt_handoff_reducer import reduce_attempt_handoffs
from zf.runtime.run_manager import run_goal_completion_gate_event


TARGET, HEAD, REPORTED = "a" * 40, "c" * 40, "a" * 41
RUN, TASK = "run-correction", "task-repair"
BASE = {"workflow_run_id": RUN, "task_map_generation": "gen-1"}
CONTRACT = {**BASE, "task_id": TASK, "pipeline_key": "pipeline", "source_branch": "topic",
            "task_ref": "task/repair", "contract_revision": "rev", "contract_snapshot_digest": "digest"}


def event(kind, key, payload, *, task=TASK, cause=None, actor="zf-cli", origin="kernel"):
    return ZfEvent(type=kind, id=key, actor=actor, origin=origin, task_id=task, correlation_id=RUN,
                   causation_id=cause, payload=payload)


def task_result(key, commit, feedback, *, verify=False, generation=2):
    stage, actor = ("verify", "verifier") if verify else ("impl", "developer")
    identity = {**CONTRACT, "operation_id": f"op-{key}", "dispatch_id": f"dispatch-{key}",
                "request_hash": f"req-{key}", "attempt_id": f"attempt-{key}",
                "task_pipeline_stage": stage, "operation_generation": generation}
    descriptor = {"ref": "feedback/1", "sha256": "1" * 64}
    control = {"ref": f"results/{key}", "sha256": "2" * 64}
    admission = {"semantic_verdict": "passed", "admission_status": "admitted", "source_event_id": key,
                 "mode": "blocking", "control_result_ref": control, "control_result_schema":
                 "verification-result.v1" if verify else "implementation-result.v1"}
    return [
        event("task.dispatched", f"send-{key}", {**identity, "role_instance": actor,
              "rework_feedback": feedback, "candidate_repair_feedback_ref": descriptor}),
        event("workflow.call.result.admitted", f"admit-{key}", {**identity, **admission}),
        event("task.pipeline.verify.completed" if verify else "dev.build.done", key,
              {**identity, "control_result_ref": control, "semantic_submit_admission_event_id": f"admit-{key}",
               "target_commit" if verify else "source_commit": commit}, actor=actor, origin=""),
    ]


def history():
    fan = {**BASE, "fanout_id": "verify-readers"}
    return [
        event("run.goal.started", "goal", {"run_id": RUN}, task=None),
        event("task.rework.requested", "request", {**BASE, "source": "task_pipeline_operator_retry",
              "task_id": TASK, "source_event_id": "original-block"}, cause="original-block"),
        *task_result("wrong", REPORTED, [{"blocking_event_id": "request", "source_event_id": "original-block"}],
                     generation=1),
        event("task.ref.rejected", "rejected", {**CONTRACT, "source_commit": REPORTED,
              "trigger_event_id": "wrong"}, cause="wrong"),
        *task_result("right", TARGET, [{"blocking_event_id": "rejected", "source_event_id": "wrong"}]),
        event("task.ref.updated", "updated", {**CONTRACT, "source_commit": TARGET, "trigger_event_id": "right"},
              cause="right"),
        *task_result("verified", TARGET, [], verify=True),
        event("dispatch.terminal.recorded", "settled", {"dispatch_id": "dispatch-verified",
              "event_id": "verified", "event_type": "task.pipeline.verify.completed"}, cause="verified"),
        event("candidate.ready", "freeze", {**BASE, "candidate_head_commit": HEAD,
              "candidate_ref": "candidate/main", "completed_task_ids": [TASK]}, task=None),
        event("fanout.started", "start", {**fan, "target_ref": "candidate/main", "trigger_event_id": "freeze"}, task=None, cause="freeze"),
        event("fanout.aggregate.completed", "aggregate", {**fan, "status": "completed",
              "success_event": "test.passed"}, task=None),
        event("test.passed", "pass", {**fan, "target_ref": "candidate/main", "status": "completed"},
              task=None, cause="aggregate"),
    ]


def test_corrected_claim_waits_for_frozen_candidate_and_passes_actual_completion_gate():
    events = history()
    claim = event("run.goal.completion.claimed", "claim", {**BASE, "run_id": RUN, "claim_id": "claim",
                  "candidate_head_commit": HEAD}, task=None)
    before = run_goal_completion_gate_event([*events[:-1], claim], claim=claim)
    assert {"open_feedback", "pending_handoff"} <= set(before.payload["blockers"])
    snapshot = reduce_attempt_handoffs(events, workflow_run_id=RUN)
    assert snapshot["open_feedback_count"] == snapshot["pending_handoff_count"] == 0
    h, = snapshot["handoffs"]
    assert h["target_commit"] == TARGET and h["ref_correction"] == {
        "reported_target_commit": REPORTED, "original_result_event_id": "wrong", "rejection_event_id": "rejected",
        "corrected_result_event_id": "right", "updated_ref_event_id": "updated", "task_verify_event_id": "verified",
        "target_commit": TARGET}
    assert run_goal_completion_gate_event([*events, claim], claim=claim).type == "run.goal.completed"
    assert reduce_attempt_handoffs(events + events[-2:], workflow_run_id=RUN) == snapshot


@pytest.mark.parametrize("key,field,value", [
    ("send-wrong", "rework_feedback", []),
    ("send-right", "rework_feedback", [{"blocking_event_id": "rejected", "source_event_id": "unrelated"}]),
    ("rejected", "trigger_event_id", "other"),
    ("updated", "source_commit", "e" * 40),
    ("send-right", "candidate_repair_feedback_ref", {"ref": "another", "sha256": "3" * 64}),
    ("send-verified", "candidate_repair_feedback_ref", {}),
    ("admit-wrong", "source_event_id", "other"), ("admit-right", "semantic_verdict", "rejected"),
    ("admit-verified", "control_result_ref", {"ref": "forged"}),
    ("verified", "task_map_generation", "other"), ("right", "contract_revision", "other"),
    ("verified", "operation_generation", 1), ("verified", "target_commit", "e" * 40),
    ("verified", "task_id", "other"), ("aggregate", "success_event", "other"),
    ("freeze", "completed_task_ids", []), ("pass", "task_map_generation", "other"),
    ("pass", "target_ref", "candidate/other"),
])
def test_mismatched_or_missing_binding_keeps_handoff_open(key, field, value):
    events = history()
    next(e for e in events if e.id == key).payload[field] = value
    snapshot = reduce_attempt_handoffs(events, workflow_run_id=RUN)
    assert snapshot["open_feedback_count"] == snapshot["pending_handoff_count"] == 1
    h, = snapshot["handoffs"]
    assert "ref_correction" not in h


def test_verifying_ones_own_fix_or_claiming_success_without_aggregate_does_not_close():
    for key in ["actor", "aggregate", "task", "trigger", "publication", "freeze"]:
        events = history()
        by_id = {e.id: e for e in events}
        if key == "actor":
            by_id["verified"].actor = by_id["send-verified"].payload["role_instance"] = "developer"
        if key == "aggregate":
            events.remove(by_id["aggregate"])
        if key == "task":
            by_id["verified"].task_id = "another"
        if key == "trigger":
            by_id["start"].causation_id = "other-event"
        if key == "publication":
            by_id["pass"].origin = "worker"
        if key == "freeze":
            by_id["freeze"].origin = "worker"
        h, = reduce_attempt_handoffs(events, workflow_run_id=RUN)["pending_handoffs"]
        assert h["target_commit"] == REPORTED


def test_old_pass_cannot_close_against_a_replaced_freeze_or_future_verify():
    for stale_freeze in (True, False):
        events = history()
        if stale_freeze:
            events.insert(-2, event("candidate.ready", "new-freeze", {
                **BASE, "candidate_head_commit": "b" * 40, "candidate_ref": "candidate/main",
                "completed_task_ids": [TASK]}, task=None))
            events[-1].payload["target_commit"] = HEAD
        else:
            verifier = next(e for e in events if e.id == "verified")
            events.remove(verifier)
            events.insert(-2, verifier)
        assert reduce_attempt_handoffs(events, workflow_run_id=RUN)["pending_handoff_count"] == 1


def test_distinct_open_feedback_on_same_task_stays_pending():
    events = history()
    events.insert(-1, event("task.rework.requested", "another-request", {**BASE, "task_id": TASK,
                  "finding_ids": ["unresolved-business-finding"]}))
    snapshot = reduce_attempt_handoffs(events, workflow_run_id=RUN)
    assert [h["request_event_id"] for h in snapshot["pending_handoffs"]] == ["another-request"]
    assert [f["finding_id"] for f in snapshot["open_feedback"]] == ["unresolved-business-finding"]
