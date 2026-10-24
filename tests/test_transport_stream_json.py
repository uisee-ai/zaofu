"""Tests for StreamJsonTransport (B1).

Uses a mock query function to avoid spawning the real claude CLI. The
real round-trip lives in tests/integration/test_stream_json_round_trip.py
behind RUN_REAL_CLAUDE=1.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest

from zf.core.config.schema import RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.session_tailer import claude_session_path
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


def test_spawn_without_launch_env_is_supported(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    transport = StreamJsonTransport(state_dir, registry, query_fn=_make_fake_query([]))
    transport.spawn(RoleConfig(name="dev"), argv=["claude"])
    assert transport.is_alive("dev") is True


def test_register_role_does_not_claim_launch_readiness(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    transport = StreamJsonTransport(state_dir, registry, query_fn=_make_fake_query([]))
    transport.register_role(RoleConfig(name="dev"))

    assert transport.is_alive("dev") is False


def test_background_dispatch_returns_before_provider_turn_completes(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    started = threading.Event()
    release = threading.Event()

    async def fake_query(*, prompt, options=None, transport=None):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        yield FakeAssistantMessage(content=[FakeTextBlock(text="done")])

    transport = StreamJsonTransport(
        state_dir,
        registry,
        query_fn=fake_query,
        background_dispatch=True,
    )
    transport.spawn(RoleConfig(name="dev"), argv=[])

    before = time.monotonic()
    transport.send_task(
        "dev",
        briefing_path=state_dir / "briefings" / "dev.md",
        prompt="go",
    )

    assert time.monotonic() - before < 0.2
    assert started.wait(1.0)
    assert transport.poll_events() == []
    release.set()

    events: list[Any] = []
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not events:
        events = transport.poll_events()
        time.sleep(0.01)
    assert any(event.type == "agent.text" for event in events)


def test_background_dispatch_serializes_turns_for_one_role(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    calls: list[str] = []

    async def fake_query(*, prompt, options=None, transport=None):
        calls.append(prompt)
        if prompt == "first":
            first_started.set()
            while not release_first.is_set():
                await asyncio.sleep(0.01)
        else:
            second_started.set()
        return
        yield

    transport = StreamJsonTransport(
        state_dir,
        registry,
        query_fn=fake_query,
        background_dispatch=True,
    )
    transport.spawn(RoleConfig(name="dev"), argv=[])
    briefing = state_dir / "briefings" / "dev.md"

    with patch.object(transport, "_wait_for_session_file", return_value=True):
        transport.send_task("dev", briefing, "first")
        assert first_started.wait(1.0)
        transport.send_task("dev", briefing, "second")

        assert second_started.wait(0.1) is False
        with pytest.raises(RuntimeError, match="dispatch queue.*full"):
            transport.send_task("dev", briefing, "third")
        release_first.set()
        assert second_started.wait(2.0)
    assert calls == ["first", "second"]


def test_background_dispatch_surfaces_provider_exception_as_event(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    async def fake_query(*, prompt, options=None, transport=None):
        raise RuntimeError("simulated transport connection failure")
        yield

    transport = StreamJsonTransport(
        state_dir,
        registry,
        query_fn=fake_query,
        background_dispatch=True,
    )
    transport.spawn(RoleConfig(name="dev", backend="claude-code"), argv=[])
    transport.send_task(
        "dev",
        state_dir / "briefings" / "dev.md",
        "go",
        context=DispatchContext(
            trace_id="trace-1",
            task_id="TASK-1",
            instance_id="dev",
        ),
    )

    events: list[Any] = []
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not events:
        events = transport.poll_events()
        time.sleep(0.01)
    blocked = next(event for event in events if event.type == "agent.api_blocked")
    assert blocked.task_id == "TASK-1"
    assert blocked.correlation_id == "trace-1"
    assert blocked.payload["provider_stop_reason"] == "transport_error"


def test_background_dispatch_preserves_claude_api_policy_error(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    async def fake_query(*, prompt, options=None, transport=None):
        raise RuntimeError("Process exited with code 1")
        yield

    transport = StreamJsonTransport(
        state_dir,
        registry,
        query_fn=fake_query,
        background_dispatch=True,
    )
    transport.spawn(RoleConfig(name="dev", backend="claude-code"), argv=[])
    session_id = str(registry.get_or_create("dev"))
    transcript = claude_session_path(str(state_dir.parent), session_id)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        json.dumps({
            "isApiErrorMessage": True,
            "apiErrorStatus": 400,
            "error": "unknown",
            "message": {
                "content": [{
                    "type": "text",
                    "text": "Request was considered high risk and rejected.",
                }],
            },
        }) + "\n",
        encoding="utf-8",
    )

    transport.send_task(
        "dev",
        state_dir / "briefings" / "dev.md",
        "go",
        context=DispatchContext(
            trace_id="trace-policy",
            task_id="TASK-POLICY",
            instance_id="dev",
        ),
    )

    events: list[Any] = []
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not events:
        events = transport.poll_events()
        time.sleep(0.01)
    blocked = next(event for event in events if event.type == "agent.api_blocked")
    assert blocked.payload["provider_stop_reason"] == "provider_policy_rejected"
    assert blocked.payload["provider_error_status"] == 400
    assert blocked.payload["provider_error_kind"] == "unknown"
    assert blocked.payload["reason"] == (
        "Request was considered high risk and rejected."
    )
    assert blocked.payload["provider_transcript_ref"] == str(transcript)
    assert "Process exited with code 1" in blocked.payload["transport_error"]


def test_spawn_passes_role_scoped_launch_env_to_sdk_without_cross_role_leakage(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    calls: list[tuple[str, Any]] = []

    async def fake_query(*, prompt, options=None, transport=None):
        calls.append((prompt, options))
        return
        yield

    transport = StreamJsonTransport(
        state_dir,
        registry,
        query_fn=fake_query,
    )
    for instance_id, token_name in (("dev-1", "one.token"), ("dev-2", "two.token")):
        role = RoleConfig(
            name="dev",
            instance_id=instance_id,
            backend="claude-code",
            transport="stream-json",
        )
        transport.register_role(role)
        transport.spawn(
            role,
            argv=[
                "env",
                f"ZF_ROLE_INSTANCE={instance_id}",
                f"ZF_ARTIFACT_READ_TOKEN_FILE={state_dir / token_name}",
                "claude",
                "--print",
                "NOT_LAUNCH_ENV=ignored",
            ],
        )
        transport.send_task(
            instance_id,
            briefing_path=state_dir / "briefings" / f"{instance_id}.md",
            prompt=instance_id,
        )

    first_env = getattr(calls[0][1], "env")
    second_env = getattr(calls[1][1], "env")
    assert first_env == {
        "ZF_ROLE_INSTANCE": "dev-1",
        "ZF_ARTIFACT_READ_TOKEN_FILE": str(state_dir / "one.token"),
    }
    assert second_env == {
        "ZF_ROLE_INSTANCE": "dev-2",
        "ZF_ARTIFACT_READ_TOKEN_FILE": str(state_dir / "two.token"),
    }
    assert "NOT_LAUNCH_ENV" not in first_env
    assert "NOT_LAUNCH_ENV" not in second_env


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
    assert getattr(opts, "permission_mode") == "bypassPermissions"
    assert getattr(opts, "allowed_tools") == ["Read", "Bash(zf events *)"]
    assert getattr(opts, "model") == "claude-sonnet-4-6"
    assert getattr(opts, "max_turns") == 17


def test_single_executor_disables_native_children_in_stream_transport(state_dir, registry):
    from zf.core.config.schema import ProviderSessionConfig

    calls = []

    async def fake_query(*, prompt, options=None, transport=None):
        calls.append(options)
        return
        yield

    role = RoleConfig(name="executor", backend="claude-code", transport="stream-json",
                      provider_session=ProviderSessionConfig(native_delegation=False))
    transport = StreamJsonTransport(state_dir, registry, query_fn=fake_query)
    transport.register_role(role)
    transport.send_task("executor", state_dir / "briefings" / "executor.md", "review")
    assert calls[0].disallowed_tools == ["Agent", "Task"]


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
    role_cwd = state_dir / "workdirs" / "dev" / "project"
    role_cwd.mkdir(parents=True)
    transport.spawn(role, argv=[], cwd=role_cwd)
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
    session_file = claude_session_path(str(role_cwd), expected_id)
    session_dir = session_file.parent
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file.write_text("{}\n")

    # Second call: resume (not session_id/extra_args)
    transport.send_task("dev", briefing_path=state_dir / "briefings" / "dev-T2.md", prompt="hello")
    assert calls[1]["options"].resume == expected_id

    # Cleanup the test session file
    session_file.unlink(missing_ok=True)


def test_second_turn_waits_for_delayed_session_file_before_purge(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    calls: list[Any] = []

    async def fake_query(*, prompt, options=None, transport=None):
        calls.append(options)
        return
        yield

    transport = StreamJsonTransport(state_dir, registry, query_fn=fake_query)
    transport.spawn(RoleConfig(name="dev"), argv=[])

    with (
        patch.object(
            transport,
            "_session_exists_on_disk",
            side_effect=[False, False, False, True],
        ),
        patch(
            "zf.runtime.transport_stream_json.purge_stale_claude_session_lock",
            return_value={},
        ) as purge,
        patch("zf.runtime.transport_stream_json.time.sleep") as sleep,
    ):
        transport.send_task(
            "dev",
            briefing_path=state_dir / "briefings" / "dev-T1.md",
            prompt="first",
        )
        transport.send_task(
            "dev",
            briefing_path=state_dir / "briefings" / "dev-T2.md",
            prompt="second",
        )

    assert purge.call_count == 1
    sleep.assert_called_once()
    assert getattr(calls[0], "resume", None) is None
    assert getattr(calls[1], "resume", None) == str(
        registry.get_or_create("dev")
    )


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


def test_usage_event_carries_observed_model_and_configured_context_window(
    state_dir: Path,
    registry: RoleSessionRegistry,
):
    messages = [
        FakeAssistantMessage(content=[FakeTextBlock(text="ok")], model="k3"),
        FakeResultMessage(
            session_id="k3-session",
            usage={"input_tokens": 1000, "output_tokens": 20},
        ),
    ]
    transport = StreamJsonTransport(
        state_dir,
        registry,
        query_fn=_make_fake_query(messages),
    )
    transport.spawn(
        RoleConfig(
            name="dev",
            backend="claude-code",
            context_window_tokens=1_000_000,
        ),
        argv=[],
    )

    transport.send_task("dev", briefing_path=state_dir / "x.md", prompt="hi")

    usage = next(
        event for event in transport.poll_events()
        if event.type == "agent.usage"
    )
    assert usage.payload["model"] == "k3"
    assert usage.payload["model_context_window"] == 1_000_000
    assert usage.payload["context_usage_ratio"] == 0.001


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
