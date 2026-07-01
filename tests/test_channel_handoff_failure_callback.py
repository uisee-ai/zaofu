"""P0.2 — when request_channel_handoff calls dispatch_reply_request and the
underlying dispatch (headless or openclaw) fails, the adapter must emit
channel.handoff.failed so the handoff state machine moves off "accepted".
Without this, accepted handoffs linger and member_busy stays true.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime import channel_adapter
from zf.runtime.channel_handoff import request_channel_handoff
from zf.runtime.channel_projection import project_channel


def _seed_channel_with_two_members(state_dir: Path) -> EventWriter:
    state_dir.mkdir(parents=True, exist_ok=True)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    for member_id, provider in [
        ("dev-1", "claude-headless"),
        ("reviewer-1", "openclaw"),
    ]:
        writer.emit(
            "channel.member.added",
            actor="web",
            correlation_id="ch-zaofu",
            payload={
                "channel_id": "ch-zaofu",
                "member_id": member_id,
                "member_type": "provider_agent",
                "provider": provider,
                "backend": provider,
                "permissions": ["read", "message", "summarize"],
                "channel_role": "dev_reviewer",
            },
        )
    writer.emit(
        "channel.message.posted",
        actor="dev-1",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-1",
            "text": "I am stuck",
            "role": "assistant",
            "source": "web",
        },
    )
    return writer


def test_handoff_dispatch_crash_emits_handoff_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / ".zf"
    writer = _seed_channel_with_two_members(state_dir)

    def _crashing_openclaw(*args, **kwargs):
        raise RuntimeError("openclaw gateway down")

    monkeypatch.setattr(
        channel_adapter,
        "dispatch_openclaw_channel_reply",
        _crashing_openclaw,
    )

    result = request_channel_handoff(
        state_dir=state_dir,
        writer=writer,
        channel_id="ch-zaofu",
        thread_id="main",
        message_id="msg-1",
        member_id="dev-1",
        target_member_id="reviewer-1",
        reason="need review",
        actor="web",
        source="web",
    )

    assert result.targets == ["reviewer-1"]
    events = EventLog(state_dir / "events.jsonl").read_all()
    types = [e.type for e in events]
    assert "channel.handoff.requested" in types
    assert "channel.handoff.accepted" in types
    # The new fail-callback chain:
    assert "channel.agent.reply.failed" in types
    assert "channel.handoff.failed" in types

    handoff_failed = next(e for e in events if e.type == "channel.handoff.failed")
    assert handoff_failed.payload["channel_id"] == "ch-zaofu"
    assert handoff_failed.payload["target_member_id"] == "reviewer-1"
    assert "openclaw gateway down" in handoff_failed.payload["reason"]


def test_handoff_failed_projection_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / ".zf"
    writer = _seed_channel_with_two_members(state_dir)

    monkeypatch.setattr(
        channel_adapter,
        "dispatch_openclaw_channel_reply",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    request_channel_handoff(
        state_dir=state_dir,
        writer=writer,
        channel_id="ch-zaofu",
        thread_id="main",
        message_id="msg-1",
        member_id="dev-1",
        target_member_id="reviewer-1",
        reason="need review",
        actor="web",
        source="web",
    )

    detail = project_channel(state_dir, "ch-zaofu") or {}
    handoffs = detail.get("handoffs", [])
    statuses = {h["status"] for h in handoffs}
    # Projection's _apply_handoff status = event.type.rsplit('.', 1)[-1].
    # So channel.handoff.failed -> status="failed".
    assert "requested" in statuses
    assert "accepted" in statuses
    assert "failed" in statuses

    # Reply request also marked failed so member_busy resolves naturally.
    reply_requests = detail.get("reply_requests", [])
    assert reply_requests, "expected at least one reply_request entry"
    assert all(r.get("status") in {"failed", "rejected"} for r in reply_requests)


def test_handoff_request_event_id_threaded_to_reply_payload(
    tmp_path: Path,
) -> None:
    """The reply.requested payload must carry handoff_request_event_id so
    that future adapter failure paths can write channel.handoff.failed.
    """
    state_dir = tmp_path / ".zf"
    writer = _seed_channel_with_two_members(state_dir)

    request_channel_handoff(
        state_dir=state_dir,
        writer=writer,
        channel_id="ch-zaofu",
        thread_id="main",
        message_id="msg-1",
        member_id="dev-1",
        target_member_id="reviewer-1",
        reason="need review",
        actor="web",
        source="web",
    )

    events = EventLog(state_dir / "events.jsonl").read_all()
    requested = next(e for e in events if e.type == "channel.handoff.requested")
    reply = next(e for e in events if e.type == "channel.agent.reply.requested")
    assert reply.payload.get("handoff_request_event_id") == requested.id
