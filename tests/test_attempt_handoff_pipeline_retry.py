from __future__ import annotations

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.attempt_handoff_reducer import reduce_attempt_handoffs


def _events() -> list[ZfEvent]:
    identity = {"workflow_run_id": "run-1", "task_map_generation": "gen-1"}
    return [
        ZfEvent(id="retry", type="task.rework.requested", task_id="T-1",
                payload={**identity, "source": "task_pipeline_operator_retry"}),
        ZfEvent(id="dispatch", type="task.dispatched", task_id="T-1", payload={
            **identity, "task_pipeline_stage": "impl", "dispatch_id": "new-dispatch",
            "rework_feedback": [{"blocking_event_id": "retry"}],
        }),
        ZfEvent(id="impl", type="dev.build.done", task_id="T-1", payload={
            **identity, "dispatch_id": "new-dispatch", "source_commit": "a" * 40,
        }),
        ZfEvent(id="verify", type="test.passed", task_id="T-1", payload={
            **identity, "target_commit": "a" * 40,
        }),
    ]


def test_pipeline_retry_closes_only_after_matching_independent_verification() -> None:
    events = _events()
    assert reduce_attempt_handoffs(events[:2])["handoffs"][0]["status"] == "acknowledged"
    assert reduce_attempt_handoffs(events[:3])["handoffs"][0]["status"] == "resolution_claimed"
    closed = reduce_attempt_handoffs(events)
    assert closed["open_feedback_count"] == closed["pending_handoff_count"] == 0
    assert closed["handoffs"][0]["status"] == "verified_closed"
    assert reduce_attempt_handoffs(events) == closed


@pytest.mark.parametrize("field,value", [
    ("task_id", "other-task"), ("workflow_run_id", "other-run"),
    ("task_map_generation", "old-gen"), ("task_pipeline_stage", "verify"),
    ("rework_feedback", [{"blocking_event_id": "unknown"}]),
    ("rework_feedback", [{"source_event_id": "retry"}]),
    ("rework_feedback", "retry"),
])
def test_unrelated_pipeline_dispatch_cannot_acknowledge_retry(field: str, value: object) -> None:
    events = _events()
    if field == "task_id":
        events[1].task_id = str(value)
    else:
        events[1].payload[field] = value
    snapshot = reduce_attempt_handoffs(events)
    assert snapshot["pending_handoff_count"] == 1
    assert snapshot["handoffs"][0]["status"] == "published"


@pytest.mark.parametrize("index,field,value", [
    (2, "dispatch_id", "old-dispatch"), (2, "task_map_generation", "old-gen"),
    (3, "target_commit", "b" * 40), (3, "workflow_run_id", "other-run"),
])
def test_retry_keeps_stale_claim_or_verification_open(index: int, field: str, value: str) -> None:
    events = _events()
    events[index].payload[field] = value
    snapshot = reduce_attempt_handoffs(events)
    assert snapshot["pending_handoff_count"] == 1
    assert snapshot["stale_claims"]
