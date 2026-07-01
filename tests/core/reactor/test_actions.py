"""P0-2: Reactor action unit tests."""

from __future__ import annotations

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.reactor.actions import (
    ActionContext,
    ActionSpec,
    EmitAction,
    LogAction,
    NoOpAction,
    build_action,
)


@pytest.fixture
def ctx(tmp_path) -> ActionContext:
    return ActionContext(event_log=EventLog(tmp_path / "events.jsonl"))


def test_emit_action_appends_derived_event(tmp_path, ctx):
    action = EmitAction(target_event="derived.event")
    source = ZfEvent(type="my.trigger", actor="test", task_id="T1", payload={"x": 1})
    action.apply(source, ctx)

    events = list(ctx.event_log.read_all())
    assert len(events) == 1
    assert events[0].type == "derived.event"
    assert events[0].task_id == "T1"
    assert events[0].causation_id == source.id


def test_emit_action_respects_payload_template(ctx):
    action = EmitAction(
        target_event="derived.event",
        payload_template={"source_role": "dev"},
    )
    source = ZfEvent(type="my.trigger", task_id="T1")
    action.apply(source, ctx)
    events = list(ctx.event_log.read_all())
    assert events[0].payload == {"source_role": "dev"}


def test_log_action_does_not_write_events(ctx):
    action = LogAction(level="info", message="hello {event.type}")
    source = ZfEvent(type="my.trigger")
    action.apply(source, ctx)
    events = list(ctx.event_log.read_all())
    assert events == []  # log doesn't emit


def test_noop_action_silently_returns(ctx):
    action = NoOpAction()
    source = ZfEvent(type="any")
    assert action.apply(source, ctx) is None


def test_build_action_emit():
    action = build_action(ActionSpec(
        type="emit", params={"event": "derived"}
    ))
    assert isinstance(action, EmitAction)
    assert action.target_event == "derived"


def test_build_action_log():
    action = build_action(ActionSpec(
        type="log", params={"level": "warning", "message": "x"}
    ))
    assert isinstance(action, LogAction)
    assert action.level == "warning"


def test_build_action_noop():
    action = build_action(ActionSpec(type="noop"))
    assert isinstance(action, NoOpAction)


def test_build_action_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown action"):
        build_action(ActionSpec(type="mystery"))


def test_build_action_emit_requires_event():
    with pytest.raises(ValueError, match="requires 'event'"):
        build_action(ActionSpec(type="emit", params={}))
