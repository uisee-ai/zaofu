"""A closed provider turn must not overtake a durable reader result."""

from pathlib import Path

import pytest

from zf.core.events import EventWriter, ZfEvent
from tests.test_reader_fanout_runtime import _start_fanout, _state


@pytest.mark.parametrize("failed", [False, True])
def test_reader_replays_result_before_recovering_closed_turn(
    tmp_path: Path, failed: bool,
) -> None:
    _, log, transport, orch = _state(
        tmp_path, child_success_event="research.child.completed",
        child_failure_event="research.child.failed",
    )
    _start_fanout(orch)
    dispatch = next(e for e in log.read_all()
                    if e.type == "fanout.child.dispatched"
                    and e.payload["child_id"] == "review-a")
    writer = EventWriter(log)
    result = writer.append(ZfEvent(
        type="research.child.failed" if failed else "research.child.completed",
        actor="review-a", payload={
            **{key: dispatch.payload[key] for key in ("fanout_id", "child_id", "run_id")},
            "status": "failed" if failed else "completed",
            "report": {"status": "failed" if failed else "passed", "summary": "local review"},
        },
    ))
    writer.append(ZfEvent(type="provider.turn.closed", actor="review-a",
                         payload={"backend": "codex", "provider_status": "completed",
                                  "turn_id": "turn-durable-result"}))

    orch._recover_unrecorded_reader_fanout_results()

    events = log.read_all()
    assert not any(e.type == "fanout.child.dispatch_lost" for e in events)
    assert any(e.type == ("fanout.child.failed" if failed else "fanout.child.completed")
               and e.causation_id == result.id for e in events)
    assert [role for role, *_ in transport.sent].count("review-a") == 1
