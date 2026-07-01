"""Unit tests for housekeeping.promote_to_memory_note_event.

Verifies that select system events (candidate.conflict, dev.blocked) are
auto-promoted into memory.note events with the correct payload shape.
Non-promotable events return None.
"""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.housekeeping import promote_to_memory_note_event


def test_candidate_conflict_promotes_to_context_memory_note():
    trigger = ZfEvent(
        type="candidate.conflict",
        actor="zf-cli",
        task_id="TASK-RNS24",
        payload={
            "pdd_id": "F-2a9bba87",
            "branch": "candidate/F-2a9bba87",
            "failed_task_id": "TASK-RNS24",
            "base_commit": "eceda142d95b7b0694b7fb37fb0697e722b63be4",
            "conflict_files": ["packages/core/package.json"],
        },
    )
    note = promote_to_memory_note_event(trigger)
    assert note is not None
    assert note.type == "memory.note"
    assert note.actor is None  # shared memory
    assert note.causation_id == trigger.id
    assert note.payload["mem_type"] == "context"
    assert note.payload["source"] == "auto_promote"
    assert note.payload["trigger_event_id"] == trigger.id
    assert note.payload["trigger_event_type"] == "candidate.conflict"
    assert "packages/core/package.json" in note.payload["content"]
    assert "TASK-RNS24" in note.payload["content"]
    assert "candidate/F-2a9bba87" in note.payload["content"]


def test_candidate_conflict_with_missing_fields_uses_placeholders():
    trigger = ZfEvent(type="candidate.conflict", payload={})
    note = promote_to_memory_note_event(trigger)
    assert note is not None
    assert note.payload["mem_type"] == "context"
    assert "?" in note.payload["content"]


def test_dev_blocked_promotes_to_fix_memory_note():
    trigger = ZfEvent(
        type="dev.blocked",
        actor="dev-1",
        task_id="TASK-RNS31",
        payload={"reason": "pnpm install requires offline mirror"},
    )
    note = promote_to_memory_note_event(trigger)
    assert note is not None
    assert note.type == "memory.note"
    assert note.causation_id == trigger.id
    assert note.payload["mem_type"] == "fix"
    assert note.payload["source"] == "auto_promote"
    assert "TASK-RNS31" in note.payload["content"]
    assert "pnpm install requires offline mirror" in note.payload["content"]


def test_dev_blocked_falls_back_to_summary_then_error_then_default():
    base = {"summary": "summary fallback"}
    note = promote_to_memory_note_event(
        ZfEvent(type="dev.blocked", task_id="T", payload=base),
    )
    assert note is not None and "summary fallback" in note.payload["content"]

    note = promote_to_memory_note_event(
        ZfEvent(type="dev.blocked", task_id="T", payload={"error": "boom"}),
    )
    assert note is not None and "boom" in note.payload["content"]

    note = promote_to_memory_note_event(
        ZfEvent(type="dev.blocked", task_id="T", payload={}),
    )
    assert note is not None and "unspecified" in note.payload["content"]


def test_non_promotable_event_returns_none():
    for event_type in [
        "dev.build.done",
        "review.approved",
        "test.passed",
        "judge.passed",
        "memory.note",  # don't re-promote our own output
        "task.assigned",
        "task.dispatched",
        "candidate.integration.completed",
    ]:
        note = promote_to_memory_note_event(
            ZfEvent(type=event_type, payload={"x": 1}),
        )
        assert note is None, f"{event_type} should not be promoted"


def test_promoted_note_preserves_correlation_id():
    trigger = ZfEvent(
        type="candidate.conflict",
        correlation_id="corr-abc",
        payload={"conflict_files": ["a"]},
    )
    note = promote_to_memory_note_event(trigger)
    assert note is not None
    assert note.correlation_id == "corr-abc"
