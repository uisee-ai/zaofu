"""Actual stream transport facts survive loss of its in-memory event queue."""
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_provider_turn_error import runtime_case
from tests.test_transport_stream_json import FakeResultMessage, FakeAssistantMessage, FakeTextBlock, FakeToolUseBlock, _make_fake_query
from zf.core.events import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.transport import DispatchContext
from zf.runtime.transport_stream_json import StreamJsonTransport


def stream_case(tmp_path, messages=None):
    runtime, store, attempt, transport = runtime_case(tmp_path)
    runtime.config.roles[0].publishes = ["verify.passed"]
    runtime.event_writer.append(ZfEvent(type="task.dispatched", task_id="T1", origin="kernel",
        payload={"attempt_id": attempt["attempt_id"], "dispatch_id": "dispatch-1", "required_reads": []}))
    role = runtime.config.roles[0]
    role.backend = "claude-code"
    # The shared fixture opens a Codex turn; close it before switching providers.
    runtime.event_writer.append(ZfEvent(type="codex.hook.stop", actor="verify-1",
        payload={"turn_id": "turn-1", "session_id": "session-1"}))
    registry = RoleSessionRegistry(runtime.state_dir / "stream-sessions.yaml", project_root=str(tmp_path))
    stream = StreamJsonTransport(runtime.state_dir, registry, cwd=tmp_path,
        query_fn=_make_fake_query(messages if messages is not None else [FakeResultMessage()]))
    stream.spawn(role, ["claude"], cwd=tmp_path)
    context = DispatchContext(task_id="T1", instance_id="verify-1", role_name="verify-1", backend="claude-code",
        run_id="run-1", operation_id="op-1", attempt_id=attempt["attempt_id"],
        lease_id=attempt["lease_id"], dispatch_id="dispatch-1", task_pipeline_stage="verify")
    return runtime, store, attempt, transport, stream, context


def test_completed_stream_turn_is_durable_and_resumes_same_attempt_after_restart(tmp_path):
    runtime, store, attempt, transport, stream, context = stream_case(tmp_path)
    stream.send_task("verify-1", Path("briefing.md"), "do the remaining work", context=context)
    closed = [e for e in runtime.event_log.read_all() if e.type == "provider.turn.closed" and e.payload.get("backend") == "claude-code"]
    assert len(closed) == 1
    assert closed[0].payload["provider_status"] == "completed"
    assert closed[0].payload["attempt_id"] == attempt["attempt_id"]
    # No poll_events(): intentionally lose the stream's in-memory notification queue.
    restarted = Orchestrator(runtime.state_dir, runtime.config, transport)
    restarted.run_once(events=[])
    assert len(transport.sends) == 1
    assert store.get(attempt["attempt_id"])["status"] == "sent"
    assert restarted.task_store.get("T1").active_dispatch_id == "dispatch-1"
    restarted.run_once(events=[])
    assert len(transport.sends) == 1


@pytest.mark.parametrize("fault", ["pause", "new_turn", "stale_attempt", "stale_lease", "untrusted", "no_final_result", "error_result", "tool", "unknown_tool", "foreign_session"])
def test_stream_continuation_rejects_waits_and_unproven_completion(tmp_path, fault):
    messages = [FakeResultMessage()]
    if fault == "no_final_result":
        messages = [FakeAssistantMessage(content=[FakeTextBlock("partial response")])]
    if fault == "error_result":
        messages = [FakeResultMessage(is_error=True, subtype="error_max_turns")]
    if fault == "foreign_session":
        messages = [FakeResultMessage(session_id="another-provider-session")]
    if fault in {"tool", "unknown_tool"}:
        messages = [FakeAssistantMessage(content=[FakeToolUseBlock(
            id="long-test" if fault == "tool" else "", name="test", input={})]), FakeResultMessage()]
    runtime, _, _, transport, stream, context = stream_case(tmp_path, messages)
    if fault == "stale_attempt":
        context = replace(context, attempt_id="old-attempt")
    if fault == "stale_lease":
        context = replace(context, lease_id="old-lease")
    stream.send_task("verify-1", Path("briefing.md"), "work", context=context)
    if fault == "pause":
        runtime.event_writer.append(ZfEvent(type="dispatch.paused", actor="operator"))
    if fault == "new_turn":
        runtime.event_writer.append(ZfEvent(type="provider.turn.opened", origin="kernel", actor="verify-1",
            payload={**context.to_payload(), "backend": "claude-code", "turn_id": "later", "session_id": "new-session"}))
    if fault == "untrusted":
        previous = [e for e in runtime.event_log.read_all() if e.type == "provider.turn.closed"][-1]
        runtime.event_writer.append(replace(previous, id="untrusted-close", origin="worker"))
    runtime.run_once(events=[])
    assert not transport.sends


def test_provider_exception_is_durable_without_copying_error_body(tmp_path):
    runtime, _, _, transport, stream, context = stream_case(tmp_path)
    async def broken(**kwargs):
        raise RuntimeError("secret-body-must-not-be-persisted")
        yield  # an SDK-compatible async iterator
    stream._query_fn = broken
    with pytest.raises(RuntimeError):
        stream.send_task("verify-1", Path("briefing.md"), "private prompt", context=context)
    event = runtime.event_log.read_all()[-1]
    assert event.type == "provider.turn.closed" and event.payload["provider_status"] == "failed"
    assert "secret-body" not in str(event.payload) and "private prompt" not in str(event.payload)
    runtime.run_once(events=[])
    assert not transport.sends


def test_open_stream_query_is_not_nudged_while_a_long_tool_waits(tmp_path):
    import asyncio
    from threading import Event
    runtime, _, _, transport, stream, context = stream_case(tmp_path)
    entered, release = Event(), Event()
    async def long_query(**kwargs):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        yield FakeResultMessage()
    stream._query_fn = long_query
    stream._background_dispatch = True
    stream.send_task("verify-1", Path("briefing.md"), "long test", context=context)
    assert entered.wait(5)
    try:
        from zf.runtime.worktree_env import run_project_setup
        setup = run_project_setup(tmp_path, "touch unsafe-rebuild")
        assert not setup.ok and not setup.ran
        assert not (tmp_path / "unsafe-rebuild").exists()
        runtime.run_once(events=[])
        assert not transport.sends
        events = runtime.event_log.read_all()
        assert any(e.type == "provider.turn.opened" for e in events)
        assert not any(e.type == "provider.turn.closed" for e in events)
    finally:
        release.set()
        for thread in list(stream._dispatch_threads.values()):
            thread.join(5)
    runtime.run_once(events=[])
    assert len(transport.sends) == 1
