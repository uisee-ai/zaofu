"""Exercise the actual Channel adapter/emitter with deterministic providers."""

from types import SimpleNamespace

import pytest

from tests.test_channel_adapter_parallel_dispatch import CH, _seed
from tests.test_channel_router import _FakeHeadlessBackend
from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter
from zf.runtime.channel_adapter import dispatch_reply_request
from zf.runtime.channel_projection import project_channel
from zf.runtime.live_delta_bus import LiveDeltaBus


def dispatch(state, writer, root, backend):
    return dispatch_reply_request(
        state_dir=state, writer=writer, channel_id=CH, request_id="req-0",
        actor="test", source="test", project_root=root,
        headless_backends={"codex-headless": backend},
    )


def test_real_adapter_retry_stream_carries_generation_through_live_and_ledger(tmp_path):
    state, writer = _seed(tmp_path, members=1, backend="codex")
    request = project_channel(state, CH)["reply_requests"][0]
    writer.emit("channel.agent.reply.failed", actor="test", payload={**request, "channel_id": CH, "reason": "timeout"})
    writer.emit("channel.agent.reply.requested", actor="test", payload={**request, "channel_id": CH, "run_generation": 2})
    observed = {}

    class Provider(_FakeHeadlessBackend):
        def run_turn(self, **kwargs):
            kwargs["on_message"](SimpleNamespace(type="thinking", content="current attempt"))
            observed["detail"] = project_channel(state, CH)
            observed["live"], _ = LiveDeltaBus(state).read_since()
            return super().run_turn(**kwargs)

    result = dispatch(state, writer, tmp_path, Provider())
    assert result.completed == ["req-0"]
    assert len(observed["detail"]["agent_session_runs"]) == 1
    assert observed["detail"]["agent_session_runs"][0]["run_generation"] == 2
    assert observed["detail"]["members"][0]["presence"] == "streaming"
    assert observed["live"] and all(e.payload.get("run_generation") == 2 for e in observed["live"])
    session_events = [e for e in writer.event_log.read_all() if e.type.startswith("agent.session.")]
    assert session_events and all(e.payload.get("run_generation") == 2 for e in session_events)
    assert project_channel(state, CH)["provider_runs"][0]["status"] == "completed"
    assert LiveDeltaBus(state).read_since()[0] == []


def test_provider_exception_closes_both_reply_and_stream(tmp_path):
    state, writer = _seed(tmp_path, members=1, backend="codex")

    class Provider(_FakeHeadlessBackend):
        def run_turn(self, **kwargs):
            kwargs["on_message"](SimpleNamespace(type="thinking", content="before exception"))
            raise RuntimeError("provider disconnected")

    result = dispatch(state, writer, tmp_path, Provider())
    assert result.failed == ["req-0"]
    assert project_channel(state, CH)["provider_runs"][0]["live_status"] == "failed"
    terminal = [e for e in writer.event_log.read_all() if e.type == "agent.session.run.failed"]
    assert len(terminal) == 1
    assert terminal[0].payload["reason"] == "provider disconnected"
    assert LiveDeltaBus(state).read_since()[0] == []


def test_unavailable_provider_does_not_leave_active_presence(tmp_path):
    state, writer = _seed(tmp_path, members=1, backend="codex")
    result = dispatch(state, writer, tmp_path, _FakeHeadlessBackend(available=False))
    assert result.failed == ["req-0"]
    detail = project_channel(state, CH)
    assert detail["running_replies"] == []
    assert detail["members"][0]["presence"] == "failed"
    assert detail["members"][0]["active_request_id"] == ""


@pytest.mark.parametrize("raises", [False, True])
def test_reply_terminated_during_provider_execution_cannot_publish_late_answer(tmp_path, raises):
    state, writer = _seed(tmp_path, members=1, backend="codex")

    class Provider(_FakeHeadlessBackend):
        def run_turn(self, **kwargs):
            request = project_channel(state, CH)["reply_requests"][0]
            writer.emit("channel.agent.reply.failed", actor="supervisor", payload={
                **request, "channel_id": CH, "reason": "superseded by new revision", "retryable": False,
            })
            if raises:
                raise RuntimeError("late provider exception")
            return super().run_turn(**kwargs)

    result = dispatch(state, writer, tmp_path, Provider())
    assert result.skipped == ["req-0"]
    detail = project_channel(state, CH)
    assert detail["reply_requests"][0]["status"] == "failed"
    assert detail["reply_requests"][0]["retryable"] is False
    assert detail["provider_runs"][0]["live_status"] == "failed"
    assert not any(e.type == "channel.agent.reply.completed" for e in writer.event_log.read_all())
    assert not any(e.type == "channel.message.posted" and e.payload.get("role") == "assistant"
                   for e in writer.event_log.read_all())


@pytest.mark.parametrize("terminal", ["complete", "fail", "cancel"])
def test_old_attempt_cleanup_preserves_retry_live_output(tmp_path, terminal):
    state, writer = _seed(tmp_path, members=1, backend="codex")
    def emitter(generation):
        return AgentSessionStreamEmitter(writer=writer, identity=AgentSessionIdentity(
            run_id="shared-run", thread_id="thread", actor="test", source="channel.headless",
            channel_id=CH, run_generation=generation,
        ))
    old, current = emitter(1), emitter(2)
    old.start()
    old.emit_message(SimpleNamespace(type="text", content="old"))
    _, cursors = LiveDeltaBus(state).read_since()
    current.start()
    current.emit_message(SimpleNamespace(type="text", content="new"))
    getattr(old, terminal)(reason="old attempt ended")
    rows, _ = LiveDeltaBus(state).read_since(cursors)
    assert [row.payload["delta"] for row in rows] == ["new"]
    assert rows[0].payload["run_generation"] == 2
