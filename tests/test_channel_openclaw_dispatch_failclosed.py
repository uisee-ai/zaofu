"""P0.1 — when openclaw dispatch raises, channel_adapter must catch it and
emit channel.agent.reply.failed (the same fail-closed pattern the headless
branch already has). Without the catch, the exception bubbles up and the
reply request silently stays in 'started' state with no observable failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime import channel_adapter
from zf.runtime.channel_adapter import dispatch_reply_request


def _seed_openclaw_member_and_request(state_dir: Path) -> EventWriter:
    state_dir.mkdir(parents=True, exist_ok=True)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.member.added",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "member_id": "openclaw-reviewer",
            "member_type": "provider_agent",
            "provider": "openclaw",
            "backend": "openclaw",
            "provider_binding_id": "remote",
            "channel_role": "dev_reviewer",
            "permissions": ["read", "message", "summarize"],
        },
    )
    writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-1",
            "text": "@openclaw-reviewer please review",
            "source": "web",
        },
    )
    writer.emit(
        "channel.agent.reply.requested",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-1",
            "message_id": "msg-1",
            "target_member_id": "openclaw-reviewer",
            "member_id": "operator",
            "status": "pending",
            "source": "web",
        },
    )
    return writer


def test_openclaw_dispatch_crash_emits_reply_failed_and_returns_failed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / ".zf"
    writer = _seed_openclaw_member_and_request(state_dir)

    def _crashing_dispatch(*args, **kwargs):
        raise RuntimeError("gateway exploded — provider_binding remote unreachable")

    monkeypatch.setattr(
        channel_adapter,
        "dispatch_openclaw_channel_reply",
        _crashing_dispatch,
    )

    result = dispatch_reply_request(
        state_dir=state_dir,
        writer=writer,
        channel_id="ch-zaofu",
        request_id="reply-1",
        actor="web",
        source="web",
    )

    assert result.failed == ["reply-1"]
    assert result.completed == []

    events = EventLog(state_dir / "events.jsonl").read_all()
    failed_events = [e for e in events if e.type == "channel.agent.reply.failed"]
    assert len(failed_events) == 1
    payload = failed_events[0].payload
    assert payload["request_id"] == "reply-1"
    assert payload["channel_id"] == "ch-zaofu"
    assert "openclaw dispatch crashed" in payload["reason"]
    assert "gateway exploded" in payload["reason"]


def test_openclaw_dispatch_crash_via_inner_attribute_error_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirror real-world case where inner build_openclaw_agent_descriptor
    or OpenClawGatewayClient() raises an AttributeError / TypeError."""
    state_dir = tmp_path / ".zf"
    writer = _seed_openclaw_member_and_request(state_dir)

    def _crashing_dispatch(*args, **kwargs):
        raise AttributeError("'NoneType' object has no attribute 'preflight'")

    monkeypatch.setattr(
        channel_adapter,
        "dispatch_openclaw_channel_reply",
        _crashing_dispatch,
    )

    result = dispatch_reply_request(
        state_dir=state_dir,
        writer=writer,
        channel_id="ch-zaofu",
        request_id="reply-1",
        actor="web",
        source="web",
    )

    assert result.failed == ["reply-1"]
    failed = [
        e for e in EventLog(state_dir / "events.jsonl").read_all()
        if e.type == "channel.agent.reply.failed"
    ]
    assert len(failed) == 1
    assert "openclaw dispatch crashed" in failed[0].payload["reason"]
