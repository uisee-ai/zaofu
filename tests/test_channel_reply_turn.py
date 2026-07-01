"""feishu B4-core: run_channel_reply_turn produces a REAL agent reply (no echo)."""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.channel_reply_turn import run_channel_reply_turn


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
