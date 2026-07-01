"""Tests for B11 — DrainStatus signaling in StreamJsonTransport.

Background (Run 3 post-mortem 2026-04-17):
  Layer 2 went silent after rate_limit_event hit before assistant produced
  any message. The old _drain swallowed the exception and returned only
  [SystemMessage(init)], which _messages_to_events filtered out, leaving
  events.jsonl with no agent record at all. Operator had no signal that
  Claude API was blocking — looked like a hang.

These tests pin the new behaviour:
  - _drain returns (collected, DrainStatus)
  - send_task emits agent.api_blocked when status==RATE_LIMITED with no
    assistant messages
  - send_task emits agent.timeout when status==TIMEOUT
  - rate_limit AFTER assistant messages does NOT emit api_blocked
    (B7's partial-progress contract preserved)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from zf.core.config.schema import RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.transport_stream_json import (
    DrainStatus,
    StreamJsonTransport,
)
from zf.runtime.transport import DispatchContext


@dataclass
class FakeAssistantMessage:
    content: list = field(default_factory=list)


@dataclass
class FakeTextBlock:
    text: str


@dataclass
class FakeSystemMessage:
    """Stand-in for the SDK's SystemMessage(init) — has neither content
    nor the ResultMessage fields, so _messages_to_events ignores it."""
    subtype: str = "init"


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    return sd


@pytest.fixture
def registry(state_dir: Path) -> RoleSessionRegistry:
    return RoleSessionRegistry(
        state_dir / "role_sessions.yaml", project_root=str(state_dir.parent)
    )


def _query_emitting(messages: list[Any], raise_after: Exception | None = None):
    async def _gen(*, prompt, options=None, transport=None):
        for m in messages:
            yield m
        if raise_after is not None:
            raise raise_after
    return _gen


def _query_hanging():
    async def _gen(*, prompt, options=None, transport=None):
        await asyncio.sleep(60)  # longer than any sane timeout
        yield FakeAssistantMessage()
    return _gen


# -- _drain status outcomes --

class TestDrainStatus:
    def test_clean_stream_returns_ok(self, state_dir, registry):
        transport = StreamJsonTransport(
            state_dir, registry,
            query_fn=_query_emitting([FakeAssistantMessage(content=[FakeTextBlock("hi")])]),
        )
        transport.spawn(RoleConfig(name="orchestrator"), argv=[])
        msgs, status = asyncio.run(transport._drain(
            prompt="x", session_id="00000000-0000-0000-0000-000000000001",
            role=RoleConfig(name="orchestrator"), is_resume=False,
        ))
        assert status == DrainStatus.OK
        assert len(msgs) == 1

    def test_rate_limit_event_returns_rate_limited(self, state_dir, registry):
        # Mid-stream rate_limit_event after assistant said something
        err = Exception("Unknown message type: rate_limit_event")
        transport = StreamJsonTransport(
            state_dir, registry,
            query_fn=_query_emitting(
                [FakeAssistantMessage(content=[FakeTextBlock("partial")])],
                raise_after=err,
            ),
        )
        transport.spawn(RoleConfig(name="orchestrator"), argv=[])
        msgs, status = asyncio.run(transport._drain(
            prompt="x", session_id="00000000-0000-0000-0000-000000000002",
            role=RoleConfig(name="orchestrator"), is_resume=False,
        ))
        assert status == DrainStatus.RATE_LIMITED
        assert len(msgs) == 1  # partial collected preserved

    def test_timeout_returns_timeout(self, state_dir, registry):
        transport = StreamJsonTransport(
            state_dir, registry,
            query_fn=_query_hanging(),
            timeout_s=0.1,
        )
        transport.spawn(RoleConfig(name="orchestrator"), argv=[])
        msgs, status = asyncio.run(transport._drain(
            prompt="x", session_id="00000000-0000-0000-0000-000000000003",
            role=RoleConfig(name="orchestrator"), is_resume=False,
        ))
        assert status == DrainStatus.TIMEOUT
        assert msgs == []  # nothing collected

    def test_unknown_error_still_raises(self, state_dir, registry):
        err = ValueError("not a rate-limit thing")
        transport = StreamJsonTransport(
            state_dir, registry,
            query_fn=_query_emitting([], raise_after=err),
        )
        transport.spawn(RoleConfig(name="orchestrator"), argv=[])
        with pytest.raises(ValueError):
            asyncio.run(transport._drain(
                prompt="x", session_id="00000000-0000-0000-0000-000000000004",
                role=RoleConfig(name="orchestrator"), is_resume=False,
            ))


# -- send_task explicit signaling --

class TestSendTaskExplicitSignals:
    def _setup(self, state_dir, registry, query_fn, timeout_s=120.0):
        transport = StreamJsonTransport(
            state_dir, registry, query_fn=query_fn, timeout_s=timeout_s,
        )
        transport.spawn(RoleConfig(name="orchestrator"), argv=[])
        return transport

    def test_rate_limit_with_no_assistant_messages_emits_api_blocked(
        self, state_dir, registry, tmp_path
    ):
        # Only SystemMessage init collected, then rate_limit_event
        err = Exception("Unknown message type: rate_limit_event")
        transport = self._setup(
            state_dir, registry,
            _query_emitting([FakeSystemMessage()], raise_after=err),
        )
        transport.send_task(
            "orchestrator", tmp_path / "briefing.md", "do thing",
        )
        events = transport.poll_events()
        types = [e.type for e in events]
        assert "agent.api_blocked" in types
        # The blocked event has the rate_limit reason
        blocked = next(e for e in events if e.type == "agent.api_blocked")
        assert "rate_limit" in blocked.payload["reason"]
        # is_alive should now report False
        assert transport.is_alive("orchestrator") is False

    def test_rate_limit_with_assistant_message_does_not_emit_api_blocked(
        self, state_dir, registry, tmp_path
    ):
        # B7 partial-progress contract preserved: assistant said something
        # before rate_limit hit → send_task treats it as success, no signal.
        err = Exception("Unknown message type: rate_limit_event")
        transport = self._setup(
            state_dir, registry,
            _query_emitting(
                [
                    FakeSystemMessage(),
                    FakeAssistantMessage(content=[FakeTextBlock("hello")]),
                ],
                raise_after=err,
            ),
        )
        transport.send_task(
            "orchestrator", tmp_path / "briefing.md", "do thing",
        )
        events = transport.poll_events()
        types = [e.type for e in events]
        assert "agent.api_blocked" not in types
        assert "agent.text" in types
        assert transport.is_alive("orchestrator") is True

    def test_timeout_emits_agent_timeout(self, state_dir, registry, tmp_path):
        transport = self._setup(
            state_dir, registry, _query_hanging(), timeout_s=0.1,
        )
        transport.send_task(
            "orchestrator", tmp_path / "briefing.md", "do thing",
        )
        events = transport.poll_events()
        types = [e.type for e in events]
        assert "agent.timeout" in types
        timeout_ev = next(e for e in events if e.type == "agent.timeout")
        assert timeout_ev.payload["timeout_s"] == 0.1
        assert transport.is_alive("orchestrator") is False

    def test_api_blocked_signal_carries_dispatch_context(
        self, state_dir, registry, tmp_path
    ):
        err = Exception("Unknown message type: rate_limit_event")
        transport = self._setup(
            state_dir,
            registry,
            _query_emitting([FakeSystemMessage()], raise_after=err),
        )
        briefing = tmp_path / "briefing.md"
        transport.send_task(
            "orchestrator",
            briefing,
            "do thing",
            context=DispatchContext(
                trace_id="trace-1",
                run_id="sess-1",
                task_id="T1",
                role_name="orchestrator",
                instance_id="orchestrator",
                backend="claude-code",
                briefing_path=briefing,
            ),
        )

        blocked = next(
            e for e in transport.poll_events() if e.type == "agent.api_blocked"
        )
        assert blocked.task_id == "T1"
        assert blocked.correlation_id == "trace-1"
        assert blocked.payload["run_id"] == "sess-1"
        assert blocked.payload["instance_id"] == "orchestrator"

    def test_timeout_signal_carries_dispatch_context(
        self, state_dir, registry, tmp_path
    ):
        transport = self._setup(
            state_dir, registry, _query_hanging(), timeout_s=0.1,
        )
        briefing = tmp_path / "briefing.md"
        transport.send_task(
            "orchestrator",
            briefing,
            "do thing",
            context=DispatchContext(
                trace_id="trace-2",
                run_id="sess-2",
                task_id="T2",
                role_name="orchestrator",
                instance_id="orchestrator",
                backend="claude-code",
                briefing_path=briefing,
            ),
        )

        timeout_ev = next(
            e for e in transport.poll_events() if e.type == "agent.timeout"
        )
        assert timeout_ev.task_id == "T2"
        assert timeout_ev.correlation_id == "trace-2"
        assert timeout_ev.payload["run_id"] == "sess-2"
        assert timeout_ev.payload["instance_id"] == "orchestrator"
