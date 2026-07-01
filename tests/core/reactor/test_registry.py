"""P0-2: EventActionRegistry unit tests."""

from __future__ import annotations

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.reactor.registry import EventActionRegistry


@pytest.fixture
def event_log(tmp_path) -> EventLog:
    return EventLog(tmp_path / "events.jsonl")


def test_empty_registry_resolves_empty(event_log):
    r = EventActionRegistry()
    assert r.resolve("anything") == []
    assert r.primary("anything") is None
    assert r.handled_events() == set()


def test_register_and_resolve(event_log):
    r = EventActionRegistry()

    def handler(event):
        return "ok"

    r.register("my.event", handler, source="test")
    entries = r.resolve("my.event")
    assert len(entries) == 1
    assert entries[0].handler is handler
    assert entries[0].source == "test"
    assert r.primary("my.event") is handler
    assert "my.event" in r.handled_events()


def test_multiple_handlers_preserve_order(event_log):
    r = EventActionRegistry()
    calls = []

    def h1(event): calls.append("h1")
    def h2(event): calls.append("h2")

    r.register("my.event", h1, source="test")
    r.register("my.event", h2, source="test")

    entries = r.resolve("my.event")
    assert len(entries) == 2
    # First-registered is primary
    assert entries[0].handler is h1
    assert entries[1].handler is h2


def test_load_yaml_actions_registers_emit(event_log):
    r = EventActionRegistry()
    r.load_yaml_actions(
        [{
            "event": "my.trigger",
            "actions": [
                {"type": "emit", "params": {"event": "derived.event"}},
            ],
        }],
        event_log,
    )
    assert "my.trigger" in r.handled_events()
    # Fire the handler
    source = ZfEvent(type="my.trigger", task_id="T1")
    r.primary("my.trigger")(source)

    events = list(event_log.read_all())
    assert len(events) == 1
    assert events[0].type == "derived.event"


def test_load_yaml_actions_skips_invalid_gracefully(event_log, caplog):
    r = EventActionRegistry()
    r.load_yaml_actions(
        [
            {"event": "valid", "actions": [{"type": "noop"}]},
            {"actions": [{"type": "noop"}]},  # missing event key
            {"event": "bad", "actions": [{"type": "mystery"}]},  # unknown type
        ],
        event_log,
    )
    # Valid one still registered
    assert "valid" in r.handled_events()
    # Invalid ones logged but don't crash


def test_yaml_action_chains_to_builtin_via_emit(event_log):
    """YAML binds custom.event → emit(dev.build.done). Verifies the
    design pattern of chaining custom events into built-in logic."""
    r = EventActionRegistry()
    r.load_yaml_actions(
        [{
            "event": "custom.milestone",
            "actions": [
                {"type": "emit", "params": {"event": "dev.build.done"}},
            ],
        }],
        event_log,
    )
    # Simulate custom event arriving
    source = ZfEvent(type="custom.milestone", task_id="T1")
    for entry in r.resolve("custom.milestone"):
        entry.handler(source)

    # Now events.jsonl should contain dev.build.done (the chained event)
    events = list(event_log.read_all())
    assert any(e.type == "dev.build.done" for e in events)


def test_len_reports_total_handlers(event_log):
    r = EventActionRegistry()
    r.register("a", lambda e: None)
    r.register("b", lambda e: None)
    r.register("b", lambda e: None)
    assert len(r) == 3
