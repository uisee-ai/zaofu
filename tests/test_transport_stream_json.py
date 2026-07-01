"""Tests for StreamJsonTransport (B1).

Uses a mock query function to avoid spawning the real claude CLI. The
real round-trip lives in tests/integration/test_stream_json_round_trip.py
behind RUN_REAL_CLAUDE=1.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from zf.core.config.schema import RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.transport import AttachHandle, DispatchContext
from zf.runtime.transport_stream_json import StreamJsonTransport


@dataclass
class FakeAssistantMessage:
    content: list = field(default_factory=list)
    model: str = "fake"
    parent_tool_use_id: str | None = None


@dataclass
class FakeTextBlock:
    text: str


@dataclass
class FakeToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass
class FakeToolResultBlock:
    tool_use_id: str
    content: Any
    is_error: bool = False


@dataclass
class FakeResultMessage:
    subtype: str = "success"
    duration_ms: int = 0
    duration_api_ms: int = 0
    is_error: bool = False
    num_turns: int = 1
    session_id: str = ""
    total_cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)
    result: str = ""


def _make_fake_query(messages: list[Any]):
    """Return an async function with the same shape as claude_code_sdk.query."""
    async def _gen(*, prompt, options=None, transport=None):
        for m in messages:
            yield m
    return _gen


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    return sd


@pytest.fixture
def registry(state_dir: Path) -> RoleSessionRegistry:
    return RoleSessionRegistry(state_dir / "role_sessions.yaml", project_root=str(state_dir.parent))


def test_spawn_is_a_noop(state_dir: Path, registry: RoleSessionRegistry):
    transport = StreamJsonTransport(state_dir, registry, query_fn=_make_fake_query([]))
    transport.spawn(RoleConfig(name="dev"), argv=["claude"])  # must not raise


def test_register_role_without_spawn_preserves_config_for_send_task(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    calls: list[Any] = []

    async def fake_query(*, prompt, options=None, transport=None):
        calls.append(options)
        return
        yield

    role = RoleConfig(
        name="orchestrator",
        backend="claude-code",
        transport="stream-json",
        permission_mode="allowlist",
        allowed_tools=["Read", "Bash(zf events *)"],
        model="claude-sonnet-4-6",
    )
    transport = StreamJsonTransport(
        state_dir,
        registry,
        query_fn=fake_query,
        max_turns=17,
    )

    transport.register_role(role)
    transport.send_task(
        "orchestrator",
        briefing_path=state_dir / "briefings" / "orchestrator.md",
        prompt="hi",
    )

    opts = calls[0]
    assert getattr(opts, "permission_mode") == "default"
    assert getattr(opts, "allowed_tools") == ["Read", "Bash(zf events *)"]
    assert getattr(opts, "model") == "claude-sonnet-4-6"
    assert getattr(opts, "max_turns") == 17


def test_send_task_first_call_uses_session_id_second_uses_resume(
    state_dir: Path, registry: RoleSessionRegistry
):
    calls: list[dict[str, Any]] = []

    async def fake_query(*, prompt, options=None, transport=None):
        calls.append({"prompt": prompt, "options": options})
        return
        yield

    transport = StreamJsonTransport(state_dir, registry, query_fn=fake_query)
    role = RoleConfig(name="dev")
    transport.spawn(role, argv=[])
    expected_id = str(registry.get_or_create("dev"))

    # First call: session-id via extra_args (not resume)
    transport.send_task("dev", briefing_path=state_dir / "briefings" / "dev-T1.md", prompt="hi")
    assert calls[0]["prompt"] == "hi"
    opts0 = calls[0]["options"]
    assert getattr(opts0, "resume", None) is None
    extra = getattr(opts0, "extra_args", None) or {}
    sid_attr = getattr(opts0, "session_id", None)
    assert extra.get("session-id") == expected_id or sid_attr == expected_id

    # Simulate Claude CLI having created the session file after first call.
    escaped = "-" + str(transport.cwd).lstrip("/").replace("/", "-")
    session_dir = Path.home() / ".claude" / "projects" / escaped
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / f"{expected_id}.jsonl").write_text("{}\n")

    # Second call: resume (not session_id/extra_args)
    transport.send_task("dev", briefing_path=state_dir / "briefings" / "dev-T2.md", prompt="hello")
    assert calls[1]["options"].resume == expected_id

    # Cleanup the test session file
    (session_dir / f"{expected_id}.jsonl").unlink(missing_ok=True)


def test_send_task_acquires_session_mutex(
    state_dir: Path, registry: RoleSessionRegistry
):
    """While send_task is in flight, the lock file for that session must exist."""
    seen_lock_state: list[bool] = []
    sid = str(registry.get_or_create("dev"))
    lock_path = state_dir / "locks" / "sessions" / f"{sid}.lock"

    async def fake_query(*, prompt, options=None, transport=None):
        seen_lock_state.append(lock_path.exists())
        return
        yield

    transport = StreamJsonTransport(state_dir, registry, query_fn=fake_query)
    transport.spawn(RoleConfig(name="dev"), argv=[])
    transport.send_task("dev", briefing_path=state_dir / "x.md", prompt="hi")
    assert seen_lock_state == [True], "lock was not held during query"


def test_capture_log_returns_recent_text(
    state_dir: Path, registry: RoleSessionRegistry
):
    msg = FakeAssistantMessage(content=[FakeTextBlock(text="hello world")])
    transport = StreamJsonTransport(
        state_dir, registry, query_fn=_make_fake_query([msg])
    )
    transport.spawn(RoleConfig(name="dev"), argv=[])
    transport.send_task("dev", briefing_path=state_dir / "x.md", prompt="hi")
    log = transport.capture_log("dev")
    assert "hello world" in log


def test_attach_handle_returns_log_tail(
    state_dir: Path, registry: RoleSessionRegistry
):
    transport = StreamJsonTransport(state_dir, registry, query_fn=_make_fake_query([]))
    handle = transport.attach_handle("dev")
    assert isinstance(handle, AttachHandle)
    assert handle.argv  # non-empty
    assert handle.argv[0] in ("less", "tail")  # tailing strategy


def test_poll_events_empty_when_no_messages(
    state_dir: Path, registry: RoleSessionRegistry
):
    transport = StreamJsonTransport(state_dir, registry, query_fn=_make_fake_query([]))
    assert transport.poll_events() == []


def test_poll_events_emits_text_block_as_agent_text(
    state_dir: Path, registry: RoleSessionRegistry
):
    msg = FakeAssistantMessage(content=[FakeTextBlock(text="reply text")])
    transport = StreamJsonTransport(
        state_dir, registry, query_fn=_make_fake_query([msg])
    )
    transport.spawn(RoleConfig(name="dev"), argv=[])
    transport.send_task("dev", briefing_path=state_dir / "x.md", prompt="hi")
    events = transport.poll_events()
    types = [e.type for e in events]
    assert "agent.text" in types
    text_event = next(e for e in events if e.type == "agent.text")
    assert text_event.actor == "dev"
    assert "reply text" in text_event.payload.get("text", "")


def test_poll_events_emits_tool_use_events(
    state_dir: Path, registry: RoleSessionRegistry
):
    msg = FakeAssistantMessage(content=[
        FakeToolUseBlock(id="tu_1", name="Read", input={"path": "src/x.py"})
    ])
    transport = StreamJsonTransport(
        state_dir, registry, query_fn=_make_fake_query([msg])
    )
    transport.spawn(RoleConfig(name="dev"), argv=[])
    transport.send_task("dev", briefing_path=state_dir / "x.md", prompt="hi")
    events = transport.poll_events()
    tool_events = [e for e in events if e.type == "agent.tool.use"]
    assert len(tool_events) == 1
    e = tool_events[0]
    assert e.actor == "dev"
    assert e.payload.get("tool") == "Read"
    assert e.payload.get("input") == {"path": "src/x.py"}
    assert e.payload.get("tool_use_id") == "tu_1"


def test_poll_events_emits_usage_from_result_message(
    state_dir: Path, registry: RoleSessionRegistry
):
    result = FakeResultMessage(
        session_id="abc",
        total_cost_usd=0.0042,
        usage={"input_tokens": 1000, "output_tokens": 200},
        num_turns=1,
    )
    transport = StreamJsonTransport(
        state_dir, registry, query_fn=_make_fake_query([result])
    )
    transport.spawn(RoleConfig(name="dev"), argv=[])
    transport.send_task("dev", briefing_path=state_dir / "x.md", prompt="hi")
    events = transport.poll_events()
    usage_events = [e for e in events if e.type == "agent.usage"]
    assert len(usage_events) == 1
    u = usage_events[0]
    assert u.payload.get("total_cost_usd") == 0.0042
    assert u.payload["usage"]["input_tokens"] == 1000


def test_provider_events_carry_dispatch_context(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    briefing = state_dir / "briefings" / "dev-1-T1.md"
    messages = [
        FakeAssistantMessage(content=[
            FakeTextBlock(text="reply text"),
            FakeToolUseBlock(id="tu_1", name="Read", input={"path": "src/x.py"}),
            FakeToolResultBlock(tool_use_id="tu_1", content="ok"),
        ]),
        FakeResultMessage(
            session_id="provider-session",
            total_cost_usd=0.0042,
            usage={"input_tokens": 1000},
        ),
    ]
    transport = StreamJsonTransport(
        state_dir, registry, query_fn=_make_fake_query(messages)
    )
    transport.spawn(RoleConfig(
        name="dev",
        instance_id="dev-1",
        backend="claude-code",
    ), argv=[])

    transport.send_task(
        "dev-1",
        briefing_path=briefing,
        prompt="hi",
        context=DispatchContext(
            trace_id="trace-1",
            run_id="sess-1",
            task_id="T1",
            role_name="dev",
            instance_id="dev-1",
            backend="claude-code",
            briefing_path=briefing,
        ),
    )

    events = transport.poll_events()
    assert {event.type for event in events} >= {
        "agent.text",
        "agent.tool.use",
        "agent.tool.result",
        "agent.usage",
    }
    for event in events:
        assert event.actor == "dev-1"
        assert event.task_id == "T1"
        assert event.correlation_id == "trace-1"
        assert event.payload["trace_id"] == "trace-1"
        assert event.payload["run_id"] == "sess-1"
        assert event.payload["role"] == "dev"
        assert event.payload["instance_id"] == "dev-1"
        assert event.payload["backend"] == "claude-code"
        assert event.payload["briefing"] == str(briefing)


def test_provider_events_carry_dispatch_id(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    briefing = state_dir / "briefings" / "dev-1-T1.md"
    messages = [FakeAssistantMessage(content=[FakeTextBlock(text="reply text")])]
    transport = StreamJsonTransport(
        state_dir, registry, query_fn=_make_fake_query(messages)
    )
    transport.spawn(RoleConfig(
        name="dev",
        instance_id="dev-1",
        backend="claude-code",
    ), argv=[])

    transport.send_task(
        "dev-1",
        briefing_path=briefing,
        prompt="hi",
        context=DispatchContext(
            trace_id="trace-1",
            task_id="T1",
            instance_id="dev-1",
            dispatch_id="disp-123",
        ),
    )

    events = transport.poll_events()
    assert events
    assert all(event.payload["dispatch_id"] == "disp-123" for event in events)


def test_rate_limit_stop_reason_is_classified(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    async def fake_query(*, prompt, options=None, transport=None):
        raise Exception("rate_limit_event")
        yield

    transport = StreamJsonTransport(state_dir, registry, query_fn=fake_query)
    transport.spawn(RoleConfig(name="dev", backend="claude-code"), argv=[])
    transport.send_task(
        "dev",
        briefing_path=state_dir / "briefings" / "dev-T1.md",
        prompt="hi",
        context=DispatchContext(task_id="T1", instance_id="dev"),
    )

    events = transport.poll_events()
    blocked = [event for event in events if event.type == "agent.api_blocked"]
    assert blocked
    assert blocked[0].payload["provider_stop_reason"] == "rate_limited"


def test_poll_events_drains_buffer(
    state_dir: Path, registry: RoleSessionRegistry
):
    """A second poll_events() call returns nothing — already drained."""
    msg = FakeAssistantMessage(content=[FakeTextBlock(text="hi")])
    transport = StreamJsonTransport(
        state_dir, registry, query_fn=_make_fake_query([msg])
    )
    transport.spawn(RoleConfig(name="dev"), argv=[])
    transport.send_task("dev", briefing_path=state_dir / "x.md", prompt="hi")
    first = transport.poll_events()
    assert first  # got events
    second = transport.poll_events()
    assert second == []


def test_send_task_with_busy_lock_raises_or_skips(
    state_dir: Path, registry: RoleSessionRegistry
):
    """A second concurrent send_task on the same role should not silently
    interleave — either it raises SessionLockBusy or it queues."""
    from zf.runtime.session_mutex import SessionLock, SessionLockBusy

    sid = str(registry.get_or_create("dev"))
    held = SessionLock(state_dir / "locks" / "sessions", sid)
    held.__enter__()
    try:
        transport = StreamJsonTransport(
            state_dir, registry, query_fn=_make_fake_query([])
        )
        with pytest.raises(SessionLockBusy):
            transport.send_task("dev", briefing_path=state_dir / "x.md", prompt="hi")
    finally:
        held.__exit__(None, None, None)
