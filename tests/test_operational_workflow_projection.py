from __future__ import annotations

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.operational_workflow_projection import (
    build_operational_workflow_projection,
    consume_projection_rebuild_requests,
)


def test_new_generation_does_not_inherit_old_terminal_status():
    events = [
        ZfEvent(
            type="fanout.aggregate.completed",
            correlation_id="run-1",
            payload={
                "fanout_id": "f1",
                "stage_id": "impl",
                "task_map_generation": "g1",
                "status": "failed",
            },
        ),
        ZfEvent(
            type="fanout.started",
            correlation_id="run-1",
            payload={
                "fanout_id": "f2",
                "stage_id": "impl",
                "task_map_generation": "g2",
            },
        ),
    ]

    projection = build_operational_workflow_projection(events)

    assert projection["rows"][0]["status"] == "failed"
    assert projection["rows"][1]["status"] == "running"


def test_rebuild_request_has_real_consumer_and_is_idempotent(tmp_path):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    request = writer.append(ZfEvent(
        type="projection.rebuild.requested",
        payload={"projection": "operational-workflow"},
    ))
    events = writer.event_log.read_all()

    assert consume_projection_rebuild_requests(
        state_dir=tmp_path,
        events=events,
        writer=writer,
    ) == 1
    assert consume_projection_rebuild_requests(
        state_dir=tmp_path,
        events=writer.event_log.read_all(),
        writer=writer,
    ) == 0
    completed = [
        event for event in writer.event_log.read_all()
        if event.type == "projection.rebuild.completed"
    ]
    assert completed[0].payload["request_event_id"] == request.id
