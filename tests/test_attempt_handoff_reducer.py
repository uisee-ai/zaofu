from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.attempt_handoff_reducer import reduce_attempt_handoffs


def _request() -> ZfEvent:
    return ZfEvent(
        id="rework-1",
        type="task.rework.requested",
        task_id="T-1",
        correlation_id="run-1",
        payload={
            "task_id": "T-1",
            "workflow_run_id": "run-1",
            "contract_revision": "rev-1",
            "task_map_generation": "gen-1",
            "dispatch_id": "dispatch-1",
            "attempt": 1,
            "feedback_id": "feedback-1",
            "finding_ids": ["finding-1"],
            "rework_feedback_ref": "artifacts/rework-feedback/T-1/f.json",
            "rework_feedback_digest": "d" * 64,
        },
    )


def _dispatch() -> ZfEvent:
    return ZfEvent(
        id="dispatch-event-1",
        type="task.dispatched",
        task_id="T-1",
        causation_id="rework-1",
        payload={
            "task_id": "T-1",
            "dispatch_id": "dispatch-1",
            "rework_request_event_id": "rework-1",
            "source": "rework_continuation",
        },
    )


def _claim(*, target: str = "b" * 40, dispatch: str = "dispatch-1") -> ZfEvent:
    return ZfEvent(
        id="claim-1",
        type="dev.build.done",
        task_id="T-1",
        payload={
            "task_id": "T-1",
            "dispatch_id": dispatch,
            "workflow_run_id": "run-1",
            "contract_revision": "rev-1",
            "task_map_generation": "gen-1",
            "source_commit": target,
            "evidence_refs": ["artifacts/test.log"],
        },
    )


def _verify(target: str, *, event_id: str = "verify-1") -> ZfEvent:
    return ZfEvent(
        id=event_id,
        type="verify.passed",
        task_id="T-1",
        payload={
            "task_id": "T-1",
            "workflow_run_id": "run-1",
            "contract_revision": "rev-1",
            "task_map_generation": "gen-1",
            "target_commit": target,
        },
    )


def test_feedback_lifecycle_requires_independent_matching_target() -> None:
    events = [_request(), _dispatch(), _claim()]
    claimed = reduce_attempt_handoffs(events)
    assert claimed["delivery_phase"] == "feedback_resolution_claimed"
    assert claimed["open_feedback_count"] == 1
    assert claimed["pending_handoff_count"] == 1

    stale = reduce_attempt_handoffs([*events, _verify("c" * 40)])
    assert stale["open_feedback_count"] == 1
    assert stale["open_feedback"][0]["status"] == "resolution_claimed"
    assert stale["stale_claims"][0]["reason"] == "verification_target_mismatch"

    closed_events = [*events, _verify("b" * 40)]
    closed = reduce_attempt_handoffs(closed_events)
    assert closed["open_feedback_count"] == 0
    assert closed["pending_handoff_count"] == 0
    assert closed["handoffs"][0]["status"] == "verified_closed"
    assert reduce_attempt_handoffs(closed_events) == closed


def test_reducer_scopes_handoffs_to_requested_workflow_run() -> None:
    run_a = "RUN-A"
    run_b = "RUN-B"
    events = [
        ZfEvent(
            type="run.goal.started",
            payload={"run_id": run_a},
            correlation_id=run_a,
        ),
        ZfEvent(
            type="run.goal.started",
            payload={"run_id": run_b},
            correlation_id=run_b,
        ),
        ZfEvent(
            id="a-rework",
            type="task.rework.requested",
            task_id="TASK-A",
            correlation_id=run_a,
            payload={"task_id": "TASK-A", "finding_ids": ["finding-a"]},
        ),
        ZfEvent(
            id="b-rework",
            type="task.rework.requested",
            task_id="TASK-B",
            correlation_id=run_b,
            payload={"task_id": "TASK-B", "finding_ids": ["finding-b"]},
        ),
    ]

    scoped = reduce_attempt_handoffs(events, workflow_run_id=run_a)

    assert [row["task_id"] for row in scoped["handoffs"]] == ["TASK-A"]
    assert [row["finding_id"] for row in scoped["open_feedback"]] == ["finding-a"]


def test_stale_worker_claim_keeps_feedback_open() -> None:
    snapshot = reduce_attempt_handoffs([
        _request(),
        _dispatch(),
        _claim(dispatch="old-dispatch"),
    ])
    assert snapshot["open_feedback"][0]["status"] == "acknowledged"
    assert snapshot["stale_claims"][0]["reason"] == "identity_mismatch"


def test_impl_lane_terminal_cannot_close_verifier_finding() -> None:
    target = "b" * 40
    impl_lane_terminal = ZfEvent(
        id="impl-lane-terminal",
        type="lane.stage.completed",
        task_id="T-1",
        actor="dev-lane-0",
        payload={
            "task_id": "T-1",
            "stage_slot": "impl",
            "target_commit": target,
        },
    )
    snapshot = reduce_attempt_handoffs([
        _request(),
        _dispatch(),
        _claim(target=target),
        impl_lane_terminal,
    ])
    assert snapshot["open_feedback_count"] == 1
    assert snapshot["open_feedback"][0]["status"] == "resolution_claimed"


def test_duplicate_events_and_new_publication_are_replay_safe() -> None:
    first = _request()
    newer = ZfEvent(
        id="rework-2",
        type="task.rework.requested",
        task_id="T-1",
        payload={
            **first.payload,
            "dispatch_id": "dispatch-2",
            "attempt": 2,
        },
    )
    snapshot = reduce_attempt_handoffs([first, first, newer])
    assert len(snapshot["handoffs"]) == 2
    assert snapshot["handoffs"][0]["status"] == "superseded"
    assert snapshot["pending_handoff_count"] == 1
