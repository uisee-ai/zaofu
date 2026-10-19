"""feishu B4-core: run_channel_reply_turn produces a REAL agent reply (no echo)."""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_turn import run_channel_reply_turn
from zf.runtime.channel_reply_prompt import fake_channel_reply_text


def _setup(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n")
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    # a channel member backed by the deterministic fake backend (real path,
    # no API keys) — claude-code/codex would give a real LLM answer + deltas.
    writer.emit("channel.member.invited", actor="web", correlation_id="ch-x",
                payload={"channel_id": "ch-x", "member_id": "dev-1",
                         "member_type": "persona", "provider": "fake",
                         "backend": "fake", "channel_role": "dev",
                         "permissions": ["read", "message"], "source": "web"})
    return state_dir, writer


def test_inbound_message_drives_real_agent_reply(tmp_path: Path):
    state_dir, writer = _setup(tmp_path)
    msg = writer.emit("channel.message.posted", actor="ou_user",
                      correlation_id="ch-x",
                      payload={"channel_id": "ch-x", "thread_id": "main",
                               "message_id": "m1", "member_id": "operator",
                               "role": "user", "source": "feishu",
                               "text": "@dev-1 介绍下你自己",
                               "refs": {"feishu": {"chat_id": "oc_o", "message_id": "om1"}}})
    out = run_channel_reply_turn(
        state_dir, writer, None, message_event=msg, message_payload=msg.payload,
        project_root=tmp_path)

    assert out["route"].reply_requests, "an @mentioned member must yield a reply request"
    assert out["dispatched"], "the reply request must be dispatched"

    events = EventLog(state_dir / "events.jsonl").read_all()
    # the agent reply is a real channel.message.posted from the member (not a
    # synthesized echo), plus the reply lifecycle.
    agent_replies = [e for e in events if e.type == "channel.message.posted"
                     and e.payload.get("member_id") == "dev-1"]
    assert agent_replies, "member produced a real reply via the backend path"
    assert any(e.type == "channel.agent.reply.requested" for e in events)
    assert any(e.type == "channel.agent.reply.completed" for e in events)
    assert all(request_id.startswith("reply-") for request_id, _ in out["dispatched"])


def test_no_member_match_yields_no_fake_reply(tmp_path: Path):
    state_dir, writer = _setup(tmp_path)
    msg = writer.emit("channel.message.posted", actor="ou_user",
                      correlation_id="ch-x",
                      payload={"channel_id": "ch-x", "thread_id": "main",
                               "message_id": "m2", "member_id": "operator",
                               "role": "user", "source": "feishu",
                               "text": "@nobody hello"})
    out = run_channel_reply_turn(
        state_dir, writer, None, message_event=msg, message_payload=msg.payload,
        project_root=tmp_path)
    assert not out["dispatched"]
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not [e for e in events if e.type == "channel.message.posted"
                and e.payload.get("member_id") == "dev-1"]


def test_unroutable_turn_does_not_drain_an_unrelated_pending_reply(tmp_path: Path):
    state_dir, writer = _setup(tmp_path)
    writer.emit(
        "channel.message.posted",
        actor="ou_user",
        correlation_id="ch-x",
        payload={
            "channel_id": "ch-x",
            "thread_id": "main",
            "message_id": "m-old",
            "member_id": "operator",
            "role": "user",
            "source": "feishu",
            "text": "@dev-1 old request",
        },
    )
    writer.emit(
        "channel.agent.reply.requested",
        actor="feishu-bridge",
        correlation_id="ch-x",
        payload={
            "channel_id": "ch-x",
            "thread_id": "main",
            "request_id": "reply-old",
            "message_id": "m-old",
            "target_member_id": "dev-1",
            "status": "pending",
            "source": "feishu",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="ou_user",
        correlation_id="ch-x",
        payload={
            "channel_id": "ch-x",
            "thread_id": "main",
            "message_id": "m-nobody",
            "member_id": "operator",
            "role": "user",
            "source": "feishu",
            "text": "@nobody hello",
        },
    )

    out = run_channel_reply_turn(
        state_dir,
        writer,
        None,
        message_event=message,
        message_payload=message.payload,
        project_root=tmp_path,
    )

    detail = project_channel(state_dir, "ch-x")
    assert out["dispatched"] == []
    assert detail is not None
    assert detail["reply_requests"][0]["status"] == "pending"


def test_turn_drains_all_queued_replies_behind_stale_pending(tmp_path: Path):
    """A bridge-only channel has no reactor to rescue a queued reply later."""
    state_dir, writer = _setup(tmp_path)
    writer.emit(
        "channel.message.posted",
        actor="ou_user",
        correlation_id="ch-x",
        payload={
            "channel_id": "ch-x",
            "thread_id": "main",
            "message_id": "m-old",
            "member_id": "operator",
            "role": "user",
            "source": "feishu",
            "text": "@dev-1 previous request",
        },
    )
    writer.emit(
        "channel.agent.reply.requested",
        actor="feishu-bridge",
        correlation_id="ch-x",
        payload={
            "channel_id": "ch-x",
            "thread_id": "main",
            "request_id": "reply-old",
            "message_id": "m-old",
            "target_member_id": "dev-1",
            "status": "pending",
            "source": "feishu",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="ou_user",
        correlation_id="ch-x",
        payload={
            "channel_id": "ch-x",
            "thread_id": "main",
            "message_id": "m-new",
            "member_id": "operator",
            "role": "user",
            "source": "feishu",
            "text": "@dev-1 latest request",
        },
    )

    out = run_channel_reply_turn(
        state_dir,
        writer,
        None,
        message_event=message,
        message_payload=message.payload,
        project_root=tmp_path,
    )

    detail = project_channel(state_dir, "ch-x")
    assert detail is not None
    states = {
        str(item["request_id"]): str(item["status"])
        for item in detail["reply_requests"]
    }
    assert states["reply-old"] == "completed"
    latest_request_id = next(
        request_id
        for request_id in states
        if request_id != "reply-old"
    )
    assert states[latest_request_id] == "completed"
    assert [request_id for request_id, _ in out["dispatched"]] == ["reply-old", latest_request_id]
def test_fake_synthesis_is_task_compilable() -> None:
    reply = fake_channel_reply_text(
        {"member_id": "synthesizer"},
        {
            "text": "Synthesize the confirmed requirement.",
            "refs": {"synthesis_request_id": "synth-1"},
        },
    )

    contract = json.loads(reply.splitlines()[-1])["channel_synthesis"]
    criterion = contract["acceptance_criteria"][0]
    command = contract["verification_commands"][0]
    assert criterion["id"] == "AC-MOCK-01"
    assert criterion["verification_command_ids"] == ["VC-MOCK-01"]
    assert command["acceptance_ids"] == ["AC-MOCK-01"]
    assert command["command"] == "test -f README.md"
    assert command["producer_paths"] == ["README.md"]
