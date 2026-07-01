"""Tests for the EventWriter append boundary."""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.signing import EventSigner


def test_event_writer_preserves_event_json_shape(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(EventLog(path))

    event = writer.emit("dev.build.done", actor="dev", task_id="T1")

    raw = json.loads(path.read_text().strip())
    assert raw["id"] == event.id
    assert raw["type"] == "dev.build.done"
    assert raw["actor"] == "dev"
    assert raw["task_id"] == "T1"
    assert "event" not in raw
    assert "sig" not in raw


def test_event_writer_can_fill_correlation_id(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(EventLog(path), correlation_id="trace-1")

    event = writer.append(ZfEvent(type="task.created", actor="zf-cli"))

    assert event.correlation_id == "trace-1"
    read_back = EventLog(path).read_all()
    assert read_back[0].correlation_id == "trace-1"


def test_event_writer_preserves_explicit_correlation_id(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(EventLog(path), correlation_id="trace-default")

    event = writer.append(ZfEvent(
        type="task.created",
        actor="zf-cli",
        correlation_id="trace-explicit",
    ))

    assert event.correlation_id == "trace-explicit"


def test_event_writer_starts_trace_for_user_message(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(EventLog(path))

    event = writer.append(ZfEvent(type="user.message", actor="human"))

    assert event.correlation_id is not None
    assert event.correlation_id.startswith("trace-")


def test_event_writer_inherits_correlation_from_causation_parent(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(EventLog(path))
    user = writer.append(ZfEvent(type="user.message", actor="human"))

    created = writer.append(ZfEvent(
        type="task.created",
        actor="zf-cli",
        task_id="T1",
        causation_id=user.id,
    ))

    assert created.correlation_id == user.correlation_id


def test_event_writer_links_task_events_to_latest_task_parent(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(EventLog(path))
    user = writer.append(ZfEvent(type="user.message", actor="human"))
    created = writer.append(ZfEvent(
        type="task.created",
        actor="zf-cli",
        task_id="T1",
        causation_id=user.id,
    ))

    dispatched = writer.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T1",
    ))

    assert dispatched.causation_id == created.id
    assert dispatched.correlation_id == user.correlation_id


def test_event_writer_uses_signer_aware_event_log(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    signer = EventSigner(b"secret")
    writer = EventWriter(EventLog(path, signer=signer))

    writer.emit("dev.build.done", actor="dev")

    raw = json.loads(path.read_text().strip())
    assert set(raw) == {"event", "sig"}
    events = EventLog(path, signer=signer).read_all()
    assert events[0].type == "dev.build.done"
