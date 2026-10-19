"""Real-review regression: queued semantic requests must not become chat supersession."""

from pathlib import Path

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_adapter import dispatch_pending_replies
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_router import route_channel_message
from zf.runtime.channel_reply_remediation import remediate_channel_replies


CH = "ch-formal-queue"


def runtime(tmp_path: Path):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    writer.emit("channel.created", actor="test", payload={"channel_id": CH, "name": "review"})
    writer.emit("channel.member.invited", actor="test", payload={
        "channel_id": CH, "member_id": "arch", "member_type": "provider_agent",
        "backend": "fake", "provider": "fake", "permissions": ["read", "message"],
    })
    return writer


def review(writer, name, *, thread="main"):
    writer.emit("channel.cross_review.requested", actor="test", payload={
        "channel_id": CH, "thread_id": thread, "request_id": name,
        "question_id": "q-" + name, "target_member_id": "arch",
    })
    return writer.emit("channel.message.posted", actor="orchestrator-reactor", payload={
        "channel_id": CH, "thread_id": thread, "message_id": "msg-" + name,
        "member_id": "operator", "role": "user", "source": "runtime", "text": "@arch Verify this fact.",
        "mentions": ["arch"], "refs": {"cross_review_request_id": name, "question_id": "q-" + name},
    })


def queued(writer, name, *, thread="main", message_id=None):
    return writer.emit("channel.agent.reply.requested", actor="test", payload={
        "channel_id": CH, "thread_id": thread, "request_id": "reply-" + name,
        "message_id": message_id or "msg-" + name, "target_member_id": "arch", "status": "queued",
    })


def test_routing_does_not_replace_a_distinct_formal_review(tmp_path):
    writer = runtime(tmp_path)
    queued(writer, "busy")
    writer.emit("channel.agent.reply.started", actor="test", payload={"channel_id": CH, "request_id": "reply-busy"})
    for name in ("one", "two", "three"):
        event = review(writer, name)
        route_channel_message(state_dir=tmp_path, writer=writer, message_event=event,
                              message_payload=event.payload, actor="orchestrator-reactor", source="runtime",
                              project_root=tmp_path, dispatch_inline=False)
    rows = project_channel(tmp_path, CH)["reply_requests"]
    assert [r["status"] for r in rows if r["request_id"] != "reply-busy"] == ["queued"] * 3
    assert [r["queue_state"] for r in rows if r["request_id"] != "reply-busy"] == ["ordered"] * 3


def test_dispatch_drains_every_formal_review_once_for_the_same_member(tmp_path):
    writer = runtime(tmp_path)
    for name in ("one", "two", "three"):
        review(writer, name)
        queued(writer, name)
    result = dispatch_pending_replies(state_dir=tmp_path, writer=writer, channel_id=CH,
                                      actor="test", source="test", allow_queued=True, project_root=tmp_path)
    assert sorted(result.completed) == ["reply-one", "reply-three", "reply-two"]
    assert result.failed == []
    assert all(row["status"] == "completed" for row in project_channel(tmp_path, CH)["cross_reviews"])


def test_chat_keeps_every_input_and_formal_work(tmp_path):
    writer = runtime(tmp_path)
    review(writer, "formal")
    queued(writer, "formal")
    queued(writer, "old-chat")
    queued(writer, "new-chat")
    queued(writer, "other-thread", thread="another")
    result = dispatch_pending_replies(state_dir=tmp_path, writer=writer, channel_id=CH,
                                      actor="test", source="test", allow_queued=True, project_root=tmp_path)
    assert sorted(result.completed) == ["reply-formal", "reply-new-chat", "reply-old-chat", "reply-other-thread"]
    assert result.failed == []


def test_mistaken_chat_supersession_rearms_only_a_live_bound_review(tmp_path):
    writer = runtime(tmp_path)
    review(writer, "lost")
    queued(writer, "lost")
    queued(writer, "ordinary")
    for name in ("lost", "ordinary"):
        writer.emit("channel.agent.reply.failed", actor="test", payload={
            "channel_id": CH, "request_id": "reply-" + name,
            "reason": "superseded by latest queued mention",
        })
    inline = []
    result = remediate_channel_replies(writer, events=writer.event_log.read_all(),
                                      dispatch=lambda *args: inline.append(args))
    assert result["redispatched"] == ["reply-lost"]
    assert inline == [], "recovery must leave provider work to the background queue"
    current = project_channel(tmp_path, CH)
    assert next(r for r in current["reply_requests"] if r["request_id"] == "reply-lost")["run_generation"] == 2
    assert remediate_channel_replies(writer, events=writer.event_log.read_all())["redispatched"] == []


def test_completed_review_and_closed_channel_are_not_resurrected(tmp_path):
    writer = runtime(tmp_path)
    review(writer, "done")
    queued(writer, "done")
    writer.emit("channel.cross_review.completed", actor="arch", payload={"channel_id": CH, "request_id": "done"})
    review(writer, "closed")
    queued(writer, "closed")
    writer.emit("channel.closed", actor="test", payload={"channel_id": CH})
    for name in ("done", "closed"):
        writer.emit("channel.agent.reply.failed", actor="test", payload={
            "channel_id": CH, "request_id": "reply-" + name, "reason": "superseded by latest queued mention",
        })
    assert remediate_channel_replies(writer, events=writer.event_log.read_all())["redispatched"] == []


@pytest.mark.parametrize("terminal", ["channel.cross_review.completed", "channel.cross_review.rejected",
                                     "channel.discussion.continued", "channel.discussion.closed",
                                     "channel.member.removed", "channel.member.suspended"])
def test_abandoned_review_obligations_are_not_resurrected(tmp_path, terminal):
    writer = runtime(tmp_path)
    review(writer, "stale")
    queued(writer, "stale")
    writer.emit("channel.agent.reply.failed", actor="test", payload={
        "channel_id": CH, "request_id": "reply-stale", "reason": "superseded by latest queued mention",
    })
    writer.emit(terminal, actor="test", payload={
        "channel_id": CH, "thread_id": "main", "request_id": "stale", "member_id": "arch",
    })
    assert remediate_channel_replies(writer, events=writer.event_log.read_all())["redispatched"] == []


def test_queue_recovery_keeps_the_existing_generation_cap(tmp_path):
    writer = runtime(tmp_path)
    review(writer, "capped")
    queued(writer, "capped")
    writer.emit("channel.agent.reply.failed", actor="test", payload={
        "channel_id": CH, "request_id": "reply-capped", "run_generation": 3,
        "reason": "superseded by latest queued mention",
    })
    result = remediate_channel_replies(writer, events=writer.event_log.read_all())
    assert result == {"redispatched": [], "exhausted": ["reply-capped"]}


def test_kernel_sweep_recovers_formal_review_through_background_dispatch(tmp_path):
    from types import SimpleNamespace
    from tests.channel_wait import wait_for_channel
    from zf.runtime.orchestrator_reactor import EventReactorMixin

    writer = runtime(tmp_path)
    review(writer, "recover")
    queued(writer, "recover")
    writer.emit("channel.agent.reply.failed", actor="test", payload={
        "channel_id": CH, "request_id": "reply-recover", "reason": "superseded by latest queued mention",
    })
    host = SimpleNamespace(state_dir=tmp_path, event_log=writer.event_log, event_writer=writer,
                           project_root=tmp_path, config=None)
    EventReactorMixin._check_channel_reply_remediation(host)
    wait_for_channel(lambda: any(e.type == "channel.cross_review.completed" for e in writer.event_log.read_all()))
    rows = project_channel(tmp_path, CH)["reply_requests"]
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["run_generation"] == 2
