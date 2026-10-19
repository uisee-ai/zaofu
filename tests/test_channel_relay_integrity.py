from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_context import build_channel_context_pack
from zf.runtime.channel_handoff import request_channel_handoff
from zf.runtime.channel_handoff_recovery import resume_unstarted_channel_handoffs
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_sidecar import channel_message_event_payload, hydrate_channel_context_pack_payload
from zf.runtime.channel_semantic_sources import build_semantic_source_bundle


def fixture(tmp_path):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    for member in ("pm", "architect", "critic"):
        writer.emit("channel.member.invited", actor="web", payload={
            "channel_id": "ch-relay", "member_id": member, "member_type": "persona",
            "backend": "fake", "provider": "fake", "channel_role": member,
            "permissions": ["read", "message"], "source": "web"})
    post(tmp_path, writer, "req", "user", "背景\n" * 1000 + "EARLY-TAIL-REQUIREMENT")
    return SimpleNamespace(state_dir=tmp_path, project_root=tmp_path, event_writer=writer,
                           event_log=writer.event_log, config=None)


def post(state, writer, message_id, role, text, thread="main"):
    return writer.emit("channel.message.posted", actor="test", payload=channel_message_event_payload(
        state, {"channel_id": "ch-relay", "thread_id": thread, "message_id": message_id,
                "member_id": "pm", "role": role, "text": text}, created_by="test"))


def test_relay_full_history_is_scoped_to_trigger_and_thread(tmp_path):
    host = fixture(tmp_path)
    post(tmp_path, host.event_writer, "a", "assistant", "分析\n" * 600 + "DISSENT-A")
    post(tmp_path, host.event_writer, "b", "assistant", "请 Critic 检查约束")
    post(tmp_path, host.event_writer, "future", "assistant", "FUTURE-SECRET")
    post(tmp_path, host.event_writer, "foreign", "user", "OTHER-THREAD-SECRET", "other")
    channel = project_channel(tmp_path, "ch-relay")
    bundle = build_semantic_source_bundle(channel, thread_id="main", trigger_message_id="b", state_dir=tmp_path)
    assert bundle["complete"] and bundle["required"]
    assert [item["message_id"] for item in bundle["documents"]] == ["req", "a", "b"]
    text = str(bundle["documents"])
    assert "EARLY-TAIL-REQUIREMENT" in text and "DISSENT-A" in text
    assert "FUTURE-SECRET" not in text and "OTHER-THREAD-SECRET" not in text


def test_blind_pack_excludes_peer_answers_and_indexes(tmp_path):
    host = fixture(tmp_path)
    post(tmp_path, host.event_writer, "peer", "assistant", "PEER-SECRET")
    channel = project_channel(tmp_path, "ch-relay")
    channel["discussions"] = {"main": {"requirement_message_id": "req", "state": "phase1_blind"}}
    channel["summary"] = "PEER-SECRET"
    channel["owner_reports"] = [{"summary": "PEER-SECRET"}]
    channel["state_updates"] = [{"summary": "PEER-SECRET", "refs": {"path": "peer-secret.md"}}]
    pack = build_channel_context_pack(channel, channel_id="ch-relay", thread_id="main",
                                      target_member_id="critic", trigger_message_id="req", state_dir=tmp_path,
                                      visibility_profile="owner_report", agent_context={"history": "PEER-SECRET"})
    assert "PEER-SECRET" not in str(pack)
    assert "EARLY-TAIL-REQUIREMENT" in str(pack["semantic_source_documents"])
    assert not pack["contribution_index"] and not pack["cross_review_index"]


@pytest.mark.parametrize("fault", ["missing", "digest", "budget"])
def test_relay_never_replaces_bad_full_source_with_a_preview(tmp_path, fault):
    host = fixture(tmp_path)
    channel = project_channel(tmp_path, "ch-relay")
    message = channel["messages"][0]
    if fault == "missing":
        (tmp_path / message["body_ref"]).unlink()
    elif fault == "digest":
        (tmp_path / message["body_ref"]).write_text("{}")
    bundle = build_semantic_source_bundle(channel, thread_id="main", trigger_message_id="req", state_dir=tmp_path,
                                          max_source_chars=10 if fault == "budget" else 100_000)
    assert bundle["required"] and not bundle["complete"]
    assert bundle["reason"] and bundle["documents"] == []


@pytest.mark.parametrize("boundary", ["channel.handoff.requested", "channel.handoff.accepted", "channel.context_pack.built"])
def test_crashed_handoff_replays_same_intent_once_and_reuses_frozen_pack(tmp_path, monkeypatch, boundary):
    host = fixture(tmp_path)
    original = host.event_writer.emit

    def crash(event_type, **kwargs):
        event = original(event_type, **kwargs)
        if event_type == boundary:
            raise RuntimeError("simulated process loss after durable write")
        return event

    monkeypatch.setattr(host.event_writer, "emit", crash)
    with pytest.raises(RuntimeError, match="process loss"):
        request_channel_handoff(state_dir=tmp_path, writer=host.event_writer, channel_id="ch-relay", thread_id="main",
                                message_id="req", member_id="pm", target_member_id="architect", reason="design",
                                actor="test", source="test", dispatch_inline=False)
    monkeypatch.setattr(host.event_writer, "emit", original)
    post(tmp_path, host.event_writer, "late", "user", "AFTER-CLAIM")
    now = datetime.now(timezone.utc) + timedelta(minutes=2)
    assert resume_unstarted_channel_handoffs(host, host.event_log.read_all(), now=now) == 1
    assert resume_unstarted_channel_handoffs(host, host.event_log.read_all(), now=now) == 0
    events = host.event_log.read_all()
    assert len([e for e in events if e.type == "channel.handoff.requested"]) == 1
    assert len([e for e in events if e.type == "channel.handoff.accepted"]) == 1
    assert len([e for e in events if e.type == "channel.agent.reply.requested"]) == 1
    packs = [e for e in events if e.type == "channel.context_pack.built"]
    assert len(packs) == 1
    pack = hydrate_channel_context_pack_payload(tmp_path, packs[0].payload, strict=True)
    assert "AFTER-CLAIM" not in str(pack["semantic_source_documents"])


def test_housekeeping_calls_handoff_recovery_without_existing_provider_requests(tmp_path, monkeypatch):
    from zf.runtime import channel_handoff_recovery, channel_message_ingress
    from zf.runtime.orchestrator_reactor import EventReactorMixin

    host = fixture(tmp_path)
    host.event_writer.emit("channel.handoff.requested", actor="test", payload={
        "channel_id": "ch-relay", "thread_id": "main", "message_id": "req",
        "member_id": "pm", "target_member_id": "critic", "reason": "review"})
    actual = resume_unstarted_channel_handoffs
    monkeypatch.setattr(channel_handoff_recovery, "resume_unstarted_channel_handoffs",
                        lambda runtime, events: actual(runtime, events, now=datetime.now(timezone.utc) + timedelta(minutes=2)))
    dispatched = []
    monkeypatch.setattr(channel_message_ingress, "dispatch_channel_replies_background", lambda **kw: dispatched.append(kw))
    EventReactorMixin._check_channel_reply_remediation(host)
    assert len(dispatched) == 1 and dispatched[0]["channel_id"] == "ch-relay"


@pytest.mark.parametrize("source", ["missing", "foreign"])
def test_handoff_rejects_missing_or_cross_thread_source(tmp_path, source):
    host = fixture(tmp_path)
    post(tmp_path, host.event_writer, "foreign", "user", "OTHER THREAD", "other")
    outcome = request_channel_handoff(state_dir=tmp_path, writer=host.event_writer,
        channel_id="ch-relay", thread_id="main", message_id=source, member_id="pm",
        target_member_id="critic", reason="review", actor="test", source="test")
    assert not outcome.reply_requests
    assert "another thread" in outcome.skipped[0]["reason"]
    assert not any(e.type == "channel.agent.reply.requested" for e in host.event_log.read_all())


def test_unreadable_body_is_an_explicit_web_error(tmp_path):
    from zf.runtime.channel_conversation_projection import project_channel_conversation
    host = fixture(tmp_path)
    message = project_channel(tmp_path, "ch-relay")["messages"][0]
    (tmp_path / message["body_ref"]).unlink()
    view = project_channel_conversation(tmp_path, "ch-relay")
    assert view["messages"][0]["body_error"]
    assert "EARLY-TAIL-REQUIREMENT" not in view["messages"][0]["text"]


@pytest.mark.parametrize("boundary", ["channel.message.posted", "channel.agent.reply.completed"])
def test_result_write_crash_preserves_evidence_without_rerunning_provider(tmp_path, monkeypatch, boundary):
    from tests.test_channel_adapter_parallel_dispatch import CH, _seed
    from zf.runtime import channel_adapter
    from zf.runtime.channel_reply_remediation import remediate_channel_replies
    from zf.runtime.channel_result_recovery import resume_unapplied_channel_results
    from zf.runtime.channel_reply_prompt import fake_channel_reply_text
    from zf.runtime.channel_sidecar import hydrate_channel_message_text

    state, writer = _seed(tmp_path, members=1, backend="codex")
    writer.emit("channel.discussion.started", actor="test", payload={
        "channel_id": CH, "thread_id": "main", "requirement_message_id": "m-1",
        "roster": ["m-0"], "product_mode": "multi_lens"})
    writer.emit("channel.agent.reply.requested", actor="test", payload={
        "channel_id": CH, "thread_id": "main", "request_id": "req-0", "message_id": "m-1",
        "target_member_id": "m-0", "status": "pending", "run_generation": 2})
    text = fake_channel_reply_text({"member_id": "m-0"}, {"text": "原文边界"})
    calls = []
    def run(**kwargs):
        calls.append(1)
        return SimpleNamespace(ok=True, reply=text, backend="codex-headless")
    monkeypatch.setattr(channel_adapter, "_run_headless_reply", run)
    original = writer.emit
    def crash(kind, **kwargs):
        event = original(kind, **kwargs)
        if kind == boundary:
            raise RuntimeError("process died after result write")
        return event
    monkeypatch.setattr(writer, "emit", crash)
    with pytest.raises(RuntimeError, match="process died"):
        channel_adapter.dispatch_reply_request(state_dir=state, writer=writer, channel_id=CH,
            request_id="req-0", actor="test", source="test", project_root=tmp_path)
    monkeypatch.setattr(writer, "emit", original)
    host = SimpleNamespace(state_dir=state, event_writer=writer, event_log=writer.event_log)
    now = datetime.now(timezone.utc) + timedelta(hours=1)
    if boundary == "channel.agent.reply.completed":
        from zf.runtime import channel_result_recovery
        from zf.runtime.orchestrator_reactor import EventReactorMixin
        monkeypatch.setattr(channel_result_recovery, "resume_unapplied_channel_results",
            lambda runtime, events: resume_unapplied_channel_results(runtime, events, now=now))
        EventReactorMixin._check_channel_reply_remediation(host)
    else:
        resume_unapplied_channel_results(host, writer.event_log.read_all(), now=now)
    assert resume_unapplied_channel_results(host, writer.event_log.read_all(), now=now) == 0
    remediate_channel_replies(writer, events=writer.event_log.read_all(), now=now)
    view = project_channel(state, CH)
    saved = next(m for m in view["messages"] if m["message_id"] == "msg-req-0-reply")
    assert hydrate_channel_message_text(state, saved, strict=True) == text
    assert calls == [1]
    assert len([e for e in writer.event_log.read_all() if e.type == "channel.agent.reply.requested"]) == 2
    if boundary == "channel.agent.reply.completed":
        assert len(view["contributions"]) == 1
        assert view["reply_requests"][0]["status"] == "completed"
    else:
        assert view["reply_requests"][0]["failure_status"] == "delivery_unknown"
