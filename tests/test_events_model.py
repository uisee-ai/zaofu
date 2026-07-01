"""Tests for ZfEvent model."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from zf.core.events.model import ZfEvent


def test_create_event_with_defaults():
    ev = ZfEvent(type="test.ping")
    assert ev.type == "test.ping"
    assert ev.id  # auto-generated
    assert ev.ts  # auto-generated
    assert ev.actor is None
    assert ev.task_id is None
    assert ev.payload == {}


def test_create_event_with_all_fields():
    ev = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-001",
        payload={"summary": "done"},
        causation_id="evt-parent",
        correlation_id="corr-1",
    )
    assert ev.type == "dev.build.done"
    assert ev.actor == "dev"
    assert ev.task_id == "TASK-001"
    assert ev.payload == {"summary": "done"}
    assert ev.causation_id == "evt-parent"
    assert ev.correlation_id == "corr-1"


def test_event_to_json_roundtrip():
    ev = ZfEvent(type="test.roundtrip", actor="arch", payload={"key": "value"})
    json_str = ev.to_json()
    data = json.loads(json_str)
    assert data["type"] == "test.roundtrip"
    assert data["actor"] == "arch"
    assert data["payload"] == {"key": "value"}

    restored = ZfEvent.from_json(json_str)
    assert restored.type == ev.type
    assert restored.id == ev.id
    assert restored.actor == ev.actor
    assert restored.payload == ev.payload


def test_event_id_is_unique():
    e1 = ZfEvent(type="a")
    e2 = ZfEvent(type="a")
    assert e1.id != e2.id


def test_event_ts_is_iso_format():
    ev = ZfEvent(type="test.ts")
    # Should parse as valid ISO datetime
    dt = datetime.fromisoformat(ev.ts)
    assert dt.tzinfo is not None  # must be timezone-aware
