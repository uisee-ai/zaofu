"""L1 (solo agent → spec) regression tests for the zaofu channel collab plan.

Covers the L1 section of docs/test_case/01-zaofu-channel-collab-test-plan.md:
- step 1: @claude-arch mention → channel.mention.detected(target=claude-arch)
- step 2: mention → channel.agent.reply.requested w/ stable request_id;
          reply.started + reply.completed link to that request_id in projection.
- step 3: synthesis.proposed.payload.spec_path is preserved by the projection;
          a transcript channel.message.posted is NOT promoted as synthesis truth.
- 反例:   text that merely mentions "spec" or contains a long pseudo-spec body
          is not synthesis truth (truth lives in synthesis.proposed only).
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_router import route_channel_message


CHANNEL_ID = "ch-zaofu"
ARCH_MEMBER_ID = "claude-arch"


def _seed_channel_with_arch(writer: EventWriter) -> None:
    """Set up an empty zaofu channel with claude-arch as a connectable member."""
    writer.emit(
        "channel.created",
        actor="web",
        correlation_id=CHANNEL_ID,
        payload={"channel_id": CHANNEL_ID, "name": "zaofu", "source": "web"},
    )
    writer.emit(
        "channel.member.added",
        actor="web",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "member_id": ARCH_MEMBER_ID,
            "member_type": "provider_agent",
            "provider": "claude",
            "backend": "claude",
            "channel_role": "arch",
            "visibility_profile": "planner",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )


def test_l1_step1_mention_detection_targets_claude_arch(tmp_path: Path) -> None:
    """L1 step 1: a @claude-arch post triggers channel.mention.detected with that target."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    _seed_channel_with_arch(writer)

    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-1",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@claude-arch 做 X 功能",
        },
    )
    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
    )

    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail is not None
    assert result.targets == [ARCH_MEMBER_ID]
    mentions = detail["mentions_detected"]
    assert len(mentions) == 1, f"expected exactly one mention.detected, got {mentions}"
    assert mentions[0]["target_member_id"] == ARCH_MEMBER_ID
    assert mentions[0]["message_id"] == "msg-1"


def test_l1_step2_reply_request_started_completed_link_by_request_id(tmp_path: Path) -> None:
    """L1 step 2: router emits reply.requested with a stable request_id; manually
    emitted reply.started + reply.completed are linked to it in the projection."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    _seed_channel_with_arch(writer)

    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-arch",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@claude-arch 出方案",
        },
    )
    route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
    )

    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail is not None
    reply_requests = detail["reply_requests"]
    assert len(reply_requests) == 1, f"expected exactly one reply request, got {reply_requests}"
    request_id = str(reply_requests[0]["request_id"])
    assert request_id, "router must emit a non-empty request_id"
    assert reply_requests[0]["target_member_id"] == ARCH_MEMBER_ID

    # Simulate backend (no real LLM): emit started then completed against the same request_id.
    writer.emit(
        "channel.agent.reply.started",
        actor="backend-sim",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": request_id,
            "message_id": "msg-arch",
            "target_member_id": ARCH_MEMBER_ID,
            "source": "web",
        },
    )
    writer.emit(
        "channel.agent.reply.completed",
        actor="backend-sim",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": request_id,
            "message_id": "msg-arch",
            "target_member_id": ARCH_MEMBER_ID,
            "reason": "ok",
            "source": "web",
        },
    )

    refreshed = project_channel(state_dir, CHANNEL_ID)
    assert refreshed is not None
    after = refreshed["reply_requests"]
    # Linkage by request_id means there is still exactly ONE entry (not three).
    assert len(after) == 1, (
        f"started + completed must merge into the same request_id row, got {after}"
    )
    assert after[0]["request_id"] == request_id
    # Status must reflect terminal "completed" because reply.completed was the last update.
    assert after[0]["status"] == "completed", (
        f"reply.completed should drive status=completed, got {after[0].get('status')!r}"
    )
    assert refreshed["pending_reply_count"] == 0


def test_l1_step3_synthesis_carries_spec_path_and_transcript_does_not(tmp_path: Path) -> None:
    """L1 step 3: synthesis.proposed payload.spec_path is preserved in projection;
    the transcript channel.message.posted entry does NOT carry spec_path
    (truth lives in synthesis, not in the message body — doc 64 §4)."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    _seed_channel_with_arch(writer)

    # Plain transcript message — must not leak spec_path even if caller smuggles it.
    writer.emit(
        "channel.message.posted",
        actor=ARCH_MEMBER_ID,
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-arch-reply",
            "member_id": ARCH_MEMBER_ID,
            "role": "assistant",
            "source": "web",
            "text": "see attached spec",
            "spec_path": "zaofu/specs/X.md",  # smuggled — must NOT be promoted
        },
    )
    # Actual synthesis carries the spec_path as authoritative truth.
    writer.emit(
        "channel.synthesis.proposed",
        actor=ARCH_MEMBER_ID,
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "decision": "create_spec",
            "summary": "X 功能方案",
            "source": "web",
            "spec_path": "zaofu/specs/X.md",
        },
    )

    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail is not None

    # Transcript projection must NOT carry spec_path on the message itself.
    message_row = detail["messages"][0]
    assert message_row["message_id"] == "msg-arch-reply"
    assert "spec_path" not in message_row, (
        f"transcript message must not be promoted as spec truth, got {message_row}"
    )

    # Synthesis truth lives in syntheses + linked_events; spec_path must survive end-to-end.
    syntheses = detail["syntheses"]
    assert len(syntheses) == 1, f"expected one synthesis, got {syntheses}"
    assert syntheses[0]["decision"] == "create_spec"

    synthesis_links = [
        item for item in detail["linked_events"]
        if item.get("type") == "channel.synthesis.proposed"
    ]
    assert len(synthesis_links) == 1
    assert synthesis_links[0]["payload"]["spec_path"] == "zaofu/specs/X.md"


def test_l1_counterexample_message_with_spec_text_is_not_synthesis_truth(tmp_path: Path) -> None:
    """反例: a channel.message.posted whose text mentions 'spec' or contains a long
    pseudo-spec body must NOT be promoted to syntheses by the projection.
    Truth lives only in channel.synthesis.proposed (doc 64 §4)."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    _seed_channel_with_arch(writer)

    long_body = "spec: X feature\n" + ("acceptance criteria line. " * 80)
    writer.emit(
        "channel.message.posted",
        actor=ARCH_MEMBER_ID,
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-fake-spec",
            "member_id": ARCH_MEMBER_ID,
            "role": "assistant",
            "source": "web",
            "text": long_body,
        },
    )

    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail is not None
    assert detail["syntheses"] == [], (
        f"channel.message.posted must never auto-promote to syntheses, got {detail['syntheses']}"
    )
    # Sanity: the message IS recorded in the transcript (so we know we exercised the path).
    assert any(row["message_id"] == "msg-fake-spec" for row in detail["messages"])
