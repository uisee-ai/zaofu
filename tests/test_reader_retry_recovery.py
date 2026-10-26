import pytest

from zf.core.events import EventWriter, ZfEvent
from tests.test_reader_fanout_runtime import _state


@pytest.mark.parametrize("new_attempt, expected_count", [(2, 1), (1, 0)])
def test_recovery_distinguishes_new_retry_from_duplicate_trigger(tmp_path, monkeypatch, new_attempt, expected_count):
    _state_dir, log, _transport, orch = _state(tmp_path)
    writer = EventWriter(log)
    payload = {"pdd_id": "PDD-1", "rework_of": "failure-1", "rework_attempt": 1}
    old = writer.append(ZfEvent(type="candidate.ready", payload=payload))
    writer.append(ZfEvent(type="fanout.started", payload={
        "fanout_id": "old-reader", "stage_id": "review-candidate", "trigger_event_id": old.id,
    }))
    writer.append(ZfEvent(type="fanout.cancelled", payload={
        "fanout_id": "old-reader", "stage_id": "review-candidate", "trigger_event_id": old.id,
    }))
    new = writer.append(ZfEvent(type="candidate.ready", payload={**payload, "rework_attempt": new_attempt}))
    writer.append(ZfEvent(type="run.dispatch.blocked", causation_id=new.id, payload={
        "source_event_id": new.id, "reason": "run_terminal:blocked",
    }))
    writer.append(ZfEvent(type="run.goal.updated", payload={"status": "active"}))
    dispatched = []

    def start(event):
        dispatched.append(event.id)
        writer.append(ZfEvent(type="fanout.started", payload={
            "fanout_id": "new-reader", "stage_id": "review-candidate", "trigger_event_id": event.id,
        }))

    monkeypatch.setattr(orch, "_maybe_start_reader_fanout", start)
    orch._resume_unstarted_reader_fanouts(log.read_all())
    assert dispatched == ([new.id] if expected_count else [])
    orch._resume_unstarted_reader_fanouts(log.read_all())
    assert len(dispatched) == expected_count
