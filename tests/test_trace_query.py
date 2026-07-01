"""Tests for correlation-aware TraceQuery."""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.trace import TraceQuery


def test_trace_query_groups_multiple_tasks_by_correlation(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    user = writer.append(ZfEvent(type="user.message", actor="human"))
    t1 = writer.append(ZfEvent(
        type="task.created",
        task_id="T1",
        causation_id=user.id,
    ))
    t2 = writer.append(ZfEvent(
        type="task.created",
        task_id="T2",
        causation_id=user.id,
    ))

    result = TraceQuery(log).by_correlation_id(user.correlation_id or "")

    assert [event.id for event in result.events] == [user.id, t1.id, t2.id]
    assert {event.task_id for event in result.events} == {None, "T1", "T2"}


def test_trace_query_rework_keeps_correlation(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    user = writer.append(ZfEvent(type="user.message", actor="human"))
    created = writer.append(ZfEvent(
        type="task.created",
        task_id="T1",
        causation_id=user.id,
    ))
    rejected = writer.append(ZfEvent(type="review.rejected", task_id="T1"))

    assert rejected.causation_id == created.id
    assert rejected.correlation_id == user.correlation_id


def test_trace_query_falls_back_to_task_id_for_legacy_events(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    first = ZfEvent(type="task.dispatched", task_id="T1")
    log.append(first)
    second = ZfEvent(type="dev.build.done", task_id="T1", causation_id=first.id)
    log.append(second)

    result = TraceQuery(log).show("T1")

    assert result.mode == "task"
    assert [event.id for event in result.events] == [first.id, second.id]


def test_trace_query_event_id_uses_causation_chain_without_correlation(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    first = ZfEvent(type="task.dispatched", task_id="T1")
    log.append(first)
    second = ZfEvent(type="dev.build.done", task_id="T1", causation_id=first.id)
    log.append(second)

    result = TraceQuery(log).show(second.id)

    assert result.mode == "causation"
    assert [event.id for event in result.events] == [first.id, second.id]
