"""Track B — kernel reactor handler for channel.message.posted.

These tests prove that raw `zf emit channel.message.posted` (i.e. an event
appended without going through ControlledActionService) is now routed by
the orchestrator reactor through the same `route_channel_message`
pipeline that the web action triggers.

The handler delegates the user/agent author gating to
`route_channel_message._auto_route_allowed` and only adds a re-entry
guard so reactor-emitted side effects do not infinite-loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


CHANNEL_ID = "ch-zaofu"


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )


@pytest.fixture
def transport() -> TmuxTransport:
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def _seed_channel(log: EventLog) -> None:
    """Two members: claude-arch (provider_agent) + codex-critic (provider_agent)."""
    log.append(ZfEvent(
        type="channel.created",
        actor="web",
        payload={"channel_id": CHANNEL_ID, "name": "zaofu", "source": "web"},
        correlation_id=CHANNEL_ID,
    ))
    log.append(ZfEvent(
        type="channel.member.added",
        actor="web",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "member_id": "claude-arch",
            "persona": "Arch",
            "member_type": "provider_agent",
            "provider": "claude-code",
            "backend": "claude-code",
            "permissions": ["read", "message"],
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    ))
    log.append(ZfEvent(
        type="channel.member.added",
        actor="web",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "member_id": "codex-critic",
            "persona": "Critic",
            "member_type": "provider_agent",
            "provider": "codex",
            "backend": "codex",
            "permissions": ["read", "message"],
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    ))


# ---------------------------------------------------------------- test_a


def test_user_posted_mention_via_reactor_routes_to_reply_requested(
    state_dir: Path, config: ZfConfig, transport: TmuxTransport,
) -> None:
    """Raw `zf emit channel.message.posted` from an operator → reactor
    must route through `route_channel_message`, emit
    `channel.mention.detected` + `channel.agent.reply.requested`."""
    log = EventLog(state_dir / "events.jsonl")
    _seed_channel(log)

    orch = Orchestrator(state_dir, config, transport)

    msg_event = orch.event_writer.emit(
        "channel.message.posted",
        actor="operator",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-op-1",
            "member_id": "operator",
            "role": "user",
            "text": "@claude-arch please plan",
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    )

    # Drive the reactor handler directly.
    decision = orch._on_channel_message_posted(msg_event)
    assert decision is None  # router emits side-effect events, no decision

    events = log.read_all()
    detected = [e for e in events if e.type == "channel.mention.detected"]
    reply_requested = [
        e for e in events if e.type == "channel.agent.reply.requested"
    ]
    assert len(detected) == 1, (
        f"expected 1 channel.mention.detected, got {[e.payload for e in detected]}"
    )
    assert detected[0].payload["target_member_id"] == "claude-arch"
    assert len(reply_requested) == 1
    assert reply_requested[0].payload["target_member_id"] == "claude-arch"


# ---------------------------------------------------------------- test_b


def test_agent_authored_message_does_not_autoroute_via_reactor(
    state_dir: Path, config: ZfConfig, transport: TmuxTransport,
) -> None:
    """Agent member posting (role=assistant) must NOT trigger autoroute.
    The reactor delegates to `_auto_route_allowed`; gate blocks the chain."""
    log = EventLog(state_dir / "events.jsonl")
    _seed_channel(log)

    orch = Orchestrator(state_dir, config, transport)

    msg_event = orch.event_writer.emit(
        "channel.message.posted",
        actor="codex-critic",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-critic-mention",
            "member_id": "codex-critic",
            "role": "assistant",
            "text": "@claude-arch fix X",
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )

    orch._on_channel_message_posted(msg_event)

    events = log.read_all()
    detected = [e for e in events if e.type == "channel.mention.detected"]
    reply_requested = [
        e for e in events if e.type == "channel.agent.reply.requested"
    ]
    assert detected == [], (
        f"agent-authored mention must not produce channel.mention.detected; "
        f"got {[e.payload for e in detected]}"
    )
    assert reply_requested == []


# ---------------------------------------------------------------- test_c


def test_reactor_self_emitted_message_does_not_reroute(
    state_dir: Path, config: ZfConfig, transport: TmuxTransport,
) -> None:
    """Re-entry guard: if a message.posted carries actor=orchestrator-reactor
    (i.e. the reactor itself authored it as part of a downstream side
    effect), the handler must short-circuit so a stuck channel.message.posted
    re-emission cannot infinite-loop the router."""
    log = EventLog(state_dir / "events.jsonl")
    _seed_channel(log)

    orch = Orchestrator(state_dir, config, transport)

    msg_event = orch.event_writer.emit(
        "channel.message.posted",
        actor="orchestrator-reactor",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-reactor-echo",
            "member_id": "operator",
            "role": "user",
            "text": "@claude-arch echoed by reactor",
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )

    orch._on_channel_message_posted(msg_event)

    events = log.read_all()
    detected = [e for e in events if e.type == "channel.mention.detected"]
    reply_requested = [
        e for e in events if e.type == "channel.agent.reply.requested"
    ]
    assert detected == [], (
        "reactor-authored channel.message.posted must not be re-routed "
        f"(loop guard); got detected={[e.payload for e in detected]}"
    )
    assert reply_requested == []
