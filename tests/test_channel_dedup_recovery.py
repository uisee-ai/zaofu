"""Kernel housekeeping recovers lost dedup dispatch without another generation."""

from datetime import datetime, timedelta, timezone

import pytest

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.channel_dedup_recovery import resume_unstarted_question_dedup
from zf.runtime.channel_projection import project_channel
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _runtime(tmp_path, monkeypatch, *, message_exists=False, age=120):
    state = tmp_path / "runtime-state"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    common = {"channel_id": "ch-recovery", "thread_id": "main", "source": "test"}
    for kind, payload in [
        ("channel.created", {"name": "Recovery"}),
        ("channel.member.added", {"member_id": "synth", "member_type": "provider_agent",
            "provider": "fake", "backend": "fake", "permissions": ["read", "message", "summarize"]}),
        ("channel.discussion.started", {"roster": ["synth"], "synthesizer": "synth", "requirement_message_id": "requirement"}),
        ("channel.question.opened", {"question_id": "q1", "question": "Which scope?", "category": "scope", "asked_by": "synth"}),
        ("channel.discussion.phase.changed", {"phase": "phase2_relay", "reason": "blind_round_complete"}),
    ]:
        writer.append(ZfEvent(type=kind, ts=old, actor="test", payload={**common, **payload}))
    writer.append(ZfEvent(type="channel.question.dedup.requested", actor="test",
        ts=(datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(), payload={**common,
            "request_id": "dedup-lost", "target_member_id": "synth", "generation": 1,
            "question_count": 1, "ledger_digest": "test-ledger"}))
    if message_exists:
        writer.emit("channel.message.posted", actor="test", payload={**common,
            "message_id": "msg-dedup-lost", "member_id": "operator", "role": "user",
            "text": "@synth consolidate", "mentions": ["synth"],
            "refs": {"question_dedup_request_id": "dedup-lost"}})
    monkeypatch.setattr("zf.runtime.channel_message_ingress.dispatch_channel_replies_background", lambda **kwargs: None)
    config = ZfConfig(project=ProjectConfig(name="recovery", state_dir=str(state)))
    return Orchestrator(state, config, TmuxTransport(TmuxSession(session_name="test", dry_run=True)))


@pytest.mark.parametrize("message_exists", [False, True])
def test_housekeeping_replays_unconsumed_request_once(tmp_path, monkeypatch, message_exists):
    runtime = _runtime(tmp_path, monkeypatch, message_exists=message_exists)
    runtime.run_once(events=[])
    runtime.run_once(events=[])
    events = runtime.event_log.read_all()
    messages = [event for event in events if event.type == "channel.message.posted"
                and event.payload.get("refs", {}).get("question_dedup_request_id") == "dedup-lost"]
    replies = [event for event in events if event.type == "channel.agent.reply.requested"
               and event.payload.get("message_id") == "msg-dedup-lost"]
    assert len(messages) == len(replies) == 1
    assert len([event for event in events if event.type == "channel.question.dedup.requested"]) == 1


@pytest.mark.parametrize("terminal", ["fresh", "closed", "resolved", "superseded"])
def test_recovery_does_not_restart_ineligible_consolidation(tmp_path, monkeypatch, terminal):
    runtime = _runtime(tmp_path, monkeypatch, age=0 if terminal == "fresh" else 120)
    common = {"channel_id": "ch-recovery", "thread_id": "main", "source": "test"}
    if terminal == "closed":
        runtime.event_writer.emit("channel.discussion.closed", actor="test", payload={**common, "outcome": "consensus"})
    elif terminal == "resolved":
        runtime.event_writer.emit("channel.question.resolved", actor="test", payload={**common,
            "question_id": "q1", "resolution": "answered", "answer": "confirmed"})
    elif terminal == "superseded":
        runtime.event_writer.emit("channel.question.dedup.requested", actor="test", payload={**common,
            "request_id": "new-request", "target_member_id": "synth", "generation": 2})
    assert resume_unstarted_question_dedup(runtime, runtime.event_log.read_all()) == 0
    assert not project_channel(runtime.state_dir, "ch-recovery")["reply_requests"]
