"""P0.4 — channel_router.route_channel_message must emit
channel.route.blocked on every early-return path so operators can observe
silent routing failures (closes review w4xl2gi11 finding rank-4).
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.channel_router import route_channel_message


def _route(state_dir: Path, writer: EventWriter, message_event: ZfEvent):
    return route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message_event,
        message_payload=message_event.payload,
        actor="web",
        source="web",
    )


def _blocked(state_dir: Path) -> list[ZfEvent]:
    return [
        e for e in EventLog(state_dir / "events.jsonl").read_all()
        if e.type == "channel.route.blocked"
    ]


def test_missing_channel_id_emits_route_blocked(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        payload={
            "thread_id": "main",
            "message_id": "msg-x",
            "text": "@dev hello",
            "source": "web",
        },
    )

    result = _route(state_dir, writer, message)

    assert result.skipped == [{"reason": "auto_route_not_allowed"}]
    blocked = _blocked(state_dir)
    assert len(blocked) == 1
    assert blocked[0].payload["reason"] == "missing_channel_id"
    assert blocked[0].payload["message_id"] == "msg-x"


def test_auto_route_not_allowed_emits_route_blocked(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    # Assistant-role messages are never auto-routed (doc 64 §5).
    message = writer.emit(
        "channel.message.posted",
        actor="dev-1",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-assistant",
            "text": "@boss please review",
            "role": "assistant",
            "source": "web",
        },
    )

    result = _route(state_dir, writer, message)

    assert result.skipped == [{"reason": "auto_route_not_allowed"}]
    blocked = _blocked(state_dir)
    assert len(blocked) == 1
    assert blocked[0].payload["reason"] == "auto_route_not_allowed"
    assert blocked[0].payload["channel_id"] == "ch-zaofu"
    assert blocked[0].correlation_id == "ch-zaofu"


def test_no_target_emits_route_blocked(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    # operator-source message, no @mention -> no_target
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-no-mention",
            "text": "just thinking out loud",
            "source": "web",
        },
    )

    result = _route(state_dir, writer, message)

    assert result.skipped == [{"reason": "no_target"}]
    blocked = _blocked(state_dir)
    assert len(blocked) == 1
    assert blocked[0].payload["reason"] == "no_target"


def test_all_no_receivers_emits_route_blocked(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    # @all in an empty channel -> all_no_receivers (reason variant)
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-all",
            "text": "@all status?",
            "source": "web",
        },
    )

    result = _route(state_dir, writer, message)

    assert result.skipped == [{"reason": "all_no_receivers"}]
    blocked = _blocked(state_dir)
    assert len(blocked) == 1
    assert blocked[0].payload["reason"] == "all_no_receivers"


def test_blocked_event_carries_causation_to_message_event(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-causation",
            "text": "no mention",
            "source": "web",
        },
    )

    _route(state_dir, writer, message)

    blocked = _blocked(state_dir)[0]
    assert blocked.causation_id == message.id
