from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.channel_contract_artifacts import persist_channel_contract
from zf.runtime.channel_conversation_projection import (
    MAX_CONVERSATION_LIMIT,
    project_channel_conversation,
)
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_sidecar import channel_message_event_payload
from zf.web.projections import read_model


CHANNEL_ID = "ch-conversation"


def _append(log: EventLog, event_type: str, payload: dict) -> None:
    log.append(ZfEvent(
        type=event_type,
        actor="test",
        correlation_id=CHANNEL_ID,
        payload={"channel_id": CHANNEL_ID, **payload},
    ))


def test_channel_conversation_projects_human_text_and_structured_card(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    _append(log, "channel.created", {"name": "Architecture"})
    _append(log, "channel.message.posted", {
        "message_id": "msg-user",
        "thread_id": "main",
        "member_id": "operator",
        "role": "user",
        "text": "Review this design",
    })
    contribution = {
        "summary": "Use one controlled gateway.",
        "findings": [{"type": "finding", "text": "One authority owns state."}],
        "risks": [{"id": "dual-write", "risk": "Two writers diverge."}],
        "questions": [{"id": "scope", "question": "Which slice ships first?"}],
    }
    reply = (
        "## Recommendation\n\nUse one controlled gateway.\n\n"
        "```json\n"
        + json.dumps({"channel_contribution": contribution}, ensure_ascii=False)
        + "\n```\n"
        + "END_MARKER"
    )
    reply_payload = channel_message_event_payload(
        state_dir,
        {
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-reply",
            "member_id": "arch",
            "role": "assistant",
            "source": "codex",
            "text": reply,
            "refs": {"request_id": "reply-1", "run_id": "run-1"},
        },
        created_by="test",
    )
    _append(log, "channel.message.posted", reply_payload)
    descriptor = persist_channel_contract(
        state_dir,
        channel_id=CHANNEL_ID,
        thread_id="main",
        identity="reply-1",
        kind="contribution",
        body=contribution,
        created_by="arch",
        source_event_id="evt-reply",
    )
    _append(log, "channel.finding.recorded", {
        "thread_id": "main",
        "member_id": "arch",
        "request_id": "reply-1",
        "message_id": "msg-user",
        "contract_status": "structured",
        "artifact_ref": descriptor["ref"],
        "artifact_digest": descriptor["sha256"],
    })

    page = project_channel_conversation(state_dir, CHANNEL_ID)

    assert page is not None
    assert page["schema_version"] == "channel.conversation.v1"
    message = page["messages"][-1]
    assert message["text"] == (
        "## Recommendation\n\nUse one controlled gateway.\n\nEND_MARKER"
    )
    assert "channel_contribution" not in message["text"]
    assert "sidecar body truncated" not in message["text"]
    assert "preview" not in message["refs"]["message_body"]
    card = message["structured_contribution"]
    assert card["summary"] == "Use one controlled gateway."
    assert card["findings"][0]["text"] == "One authority owns state."
    assert card["risks"][0]["text"] == "Two writers diverge."
    assert card["artifact_ref"] == descriptor["ref"]


def test_channel_conversation_carries_compact_discussion_attention(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    _append(log, "channel.created", {"name": "Attention"})
    _append(log, "channel.discussion.started", {
        "thread_id": "main",
        "discussion_id": "discussion-1",
        "roster": ["arch", "critic"],
        "synthesizer": "arch",
        "requirement_message_id": "msg-1",
    })
    _append(log, "channel.agent.reply.requested", {
        "thread_id": "main",
        "request_id": "reply-1",
        "message_id": "msg-1",
        "target_member_id": "arch",
        "status": "pending",
    })

    conversation = project_channel_conversation(state_dir, CHANNEL_ID)

    assert conversation is not None
    attention = conversation["discussion_attention"]["main"]
    assert attention["state"] == "running"
    assert attention["active_agent_count"] == 1
    assert attention["participant_count"] == 2
    assert attention["is_derived_projection"] is True


def test_channel_conversation_compacts_internal_synthesis_repair_message(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    _append(log, "channel.created", {"name": "Repair"})
    repair = channel_message_event_payload(
        state_dir,
        {
            "channel_id": CHANNEL_ID,
            "message_id": "msg-repair",
            "thread_id": "main",
            "member_id": "operator",
            "role": "user",
            "source": "runtime",
            "text": (
                "@synthesizer Correct synthesis contract. Contract diagnostic: "
                "do-not-render-this-path/channels/private-invalid-reply.txt"
            ),
            "mentions": ["synthesizer"],
            "refs": {
                "synthesis_request_id": "synth-1",
                "synthesis_repair_id": "repair-1",
                "synthesis_repair_revision": 2,
            },
        },
        created_by="test",
    )
    _append(log, "channel.message.posted", repair)

    conversation = project_channel_conversation(state_dir, CHANNEL_ID)

    assert conversation is not None
    message = conversation["messages"][0]
    assert message["role"] == "system"
    assert message["member_id"] == "zf-kernel"
    assert message["text"] == (
        "ZaoFu retried synthesis after contract validation failed "
        "(revision 2)."
    )
    assert "do-not-render" not in message["text"]
    assert message["refs"]["synthesis_repair_id"] == "repair-1"
    assert message["refs"]["synthesis_repair_revision"] == 2


def test_channel_conversation_paginates_without_duplicates(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    _append(log, "channel.created", {"name": "History"})
    for index in range(120):
        _append(log, "channel.message.posted", {
            "message_id": f"msg-{index:03d}",
            "thread_id": "main",
            "member_id": "operator",
            "role": "user",
            "text": f"message {index}",
        })

    latest = project_channel_conversation(state_dir, CHANNEL_ID, limit=50)
    assert latest is not None
    assert [item["message_id"] for item in latest["messages"]] == [
        f"msg-{index:03d}" for index in range(70, 120)
    ]
    assert latest["has_more"] is True
    assert latest["next_before"] == "msg-070"

    earlier = project_channel_conversation(
        state_dir,
        CHANNEL_ID,
        limit=50,
        before=latest["next_before"],
    )
    assert earlier is not None
    assert [item["message_id"] for item in earlier["messages"]] == [
        f"msg-{index:03d}" for index in range(20, 70)
    ]
    assert not ({
        item["message_id"] for item in latest["messages"]
    } & {
        item["message_id"] for item in earlier["messages"]
    })
    assert earlier["next_before"] == "msg-020"

    bounded = project_channel_conversation(state_dir, CHANNEL_ID, limit=999)
    assert bounded is not None
    assert len(bounded["messages"]) == MAX_CONVERSATION_LIMIT


def test_channel_conversation_accepts_a_scoped_read_model_event_slice(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    _append(log, "channel.created", {"name": "Indexed"})
    _append(log, "channel.discussion.started", {
        "thread_id": "main",
        "discussion_id": "discussion-indexed",
        "roster": ["arch"],
        "synthesizer": "arch",
        "requirement_message_id": "msg-indexed",
    })
    _append(log, "channel.message.posted", {
        "message_id": "msg-indexed",
        "thread_id": "main",
        "member_id": "operator",
        "role": "user",
        "text": "Use the channel index.",
    })
    log.append(ZfEvent(
        type="task.created",
        actor="test",
        payload={"task_id": "unrelated"},
    ))
    read_model.rebuild(state_dir)

    indexed_events = read_model.hydrate_events_by_ref(
        state_dir,
        ref_kind="channel",
        ref_id=CHANNEL_ID,
        require_fresh=False,
    )
    full = project_channel_conversation(state_dir, CHANNEL_ID)
    indexed = project_channel_conversation(
        state_dir,
        CHANNEL_ID,
        events=indexed_events,
    )

    assert indexed_events is not None
    assert full is not None
    assert indexed is not None
    assert indexed["messages"] == full["messages"]
    assert indexed["discussion_attention"] == full["discussion_attention"]
    assert indexed["message_count"] == full["message_count"]


def test_channel_conversation_omits_diagnostics_and_duplicate_payloads(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    _append(log, "channel.created", {"name": "Slim"})
    for index in range(12):
        _append(log, "channel.message.posted", {
            "message_id": f"msg-{index}",
            "thread_id": "main",
            "member_id": "operator",
            "role": "user",
            "text": "hello " + ("x" * 120),
        })
    for index in range(10):
        _append(log, "channel.context_pack.built", {
            "context_pack_id": f"ctx-{index}",
            "thread_id": "main",
            "summary": "diagnostic " + ("y" * 5000),
            "message_refs": [{"message_id": f"msg-{value}"} for value in range(12)],
        })

    detail = project_channel(state_dir, CHANNEL_ID)
    conversation = project_channel_conversation(state_dir, CHANNEL_ID)

    assert detail is not None
    assert conversation is not None
    assert "recent_messages" not in conversation
    assert "linked_events" not in conversation
    assert "context_packs" not in conversation
    assert conversation["agent_session_runs"] == []
    full_bytes = len(json.dumps(detail, ensure_ascii=False).encode())
    slim_bytes = len(json.dumps(conversation, ensure_ascii=False).encode())
    assert slim_bytes < full_bytes * 0.3


def test_channel_conversation_skips_linked_event_materialization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    for index in range(100):
        _append(log, "channel.message.posted", {
            "message_id": f"msg-{index}",
            "thread_id": "main",
            "member_id": "operator",
            "role": "user",
            "text": f"message {index}",
        })

    from zf.runtime import channel_projection

    materialized: list[int] = []
    original = channel_projection._event_record

    def record_event(seq, event):
        materialized.append(seq)
        return original(seq, event)

    monkeypatch.setattr(channel_projection, "_event_record", record_event)

    conversation = project_channel_conversation(state_dir, CHANNEL_ID)
    assert conversation is not None
    assert materialized == []
    assert conversation["diagnostics_summary"]["linked_event_count"] == 100

    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail is not None
    assert len(detail["linked_events"]) == 80
    assert len(materialized) == 80
