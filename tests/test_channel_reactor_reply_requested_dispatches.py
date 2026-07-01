"""Phase (ii) — kernel reactor handler for channel.agent.reply.requested.

These tests prove that raw `zf emit channel.agent.reply.requested` (i.e. an
operator dispatching to a member without going through the router) is now
picked up by the orchestrator reactor and routed through
`dispatch_reply_request`, the same helper the inline router path uses.

Idempotency: the inline router path emits `channel.agent.reply.started`
BEFORE this handler sees the event. The started-event lookup guard makes
the handler a no-op in that case, so only raw operator-emitted
reply.requested events trigger a dispatch.
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
REQUEST_ID = "reply-abc123def4567890"
MESSAGE_ID = "msg-op-1"
TARGET_MEMBER_ID = "dev-cc-1"


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


def _seed_channel(log: EventLog, *, with_member: bool = True) -> None:
    log.append(ZfEvent(
        type="channel.created",
        actor="web",
        payload={"channel_id": CHANNEL_ID, "name": "zaofu", "source": "web"},
        correlation_id=CHANNEL_ID,
    ))
    if with_member:
        log.append(ZfEvent(
            type="channel.member.added",
            actor="web",
            payload={
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "member_id": TARGET_MEMBER_ID,
                "persona": "Dev",
                # member_type=persona_agent routes the dispatch through the
                # deterministic fake-reply path, which emits
                # channel.agent.reply.started without spawning a real backend.
                "member_type": "persona_agent",
                "provider": "claude-code",
                "backend": "claude-code",
                "permissions": ["read", "message"],
                "source": "web",
            },
            correlation_id=CHANNEL_ID,
        ))
    # Original user message that motivated the reply request.
    log.append(ZfEvent(
        type="channel.message.posted",
        actor="operator",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": MESSAGE_ID,
            "member_id": "operator",
            "role": "user",
            "text": f"@{TARGET_MEMBER_ID} please review",
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    ))


def _reply_requested_payload(*, target: str = TARGET_MEMBER_ID) -> dict:
    return {
        "channel_id": CHANNEL_ID,
        "thread_id": "main",
        "request_id": REQUEST_ID,
        "message_id": MESSAGE_ID,
        "target_member_id": target,
        "member_id": "operator",
        "status": "pending",
        "queue_state": "ready",
        "context_pack_id": f"ctx-{REQUEST_ID[len('reply-'):]}",
        "member_type": "persona_agent",
        "backend": "claude-code",
        "provider": "claude-code",
        "source": "web",
    }


# ---------------------------------------------------------------- test_a


def test_raw_reply_requested_dispatches_to_target_backend(
    state_dir: Path, config: ZfConfig, transport: TmuxTransport,
) -> None:
    """Operator raw-emits channel.agent.reply.requested → reactor handler
    calls dispatch_reply_request → channel.agent.reply.started is emitted
    with the same request_id."""
    log = EventLog(state_dir / "events.jsonl")
    _seed_channel(log)

    orch = Orchestrator(state_dir, config, transport)

    requested = orch.event_writer.emit(
        "channel.agent.reply.requested",
        actor="operator",
        payload=_reply_requested_payload(),
        correlation_id=CHANNEL_ID,
    )

    decision = orch._on_channel_agent_reply_requested(requested)
    assert decision is None

    started = [
        e for e in log.read_all() if e.type == "channel.agent.reply.started"
    ]
    assert len(started) == 1, (
        f"expected 1 channel.agent.reply.started, got "
        f"{[e.payload for e in started]}"
    )
    assert started[0].payload["request_id"] == REQUEST_ID
    assert started[0].payload["target_member_id"] == TARGET_MEMBER_ID
    assert started[0].payload["channel_id"] == CHANNEL_ID


# ---------------------------------------------------------------- test_b


def test_reply_requested_handler_is_idempotent_when_started_exists(
    state_dir: Path, config: ZfConfig, transport: TmuxTransport,
) -> None:
    """If channel.agent.reply.started already exists for the same
    (channel_id, request_id) — which is what the inline router path
    produces — the reactor handler is a no-op. Count stays at 1."""
    log = EventLog(state_dir / "events.jsonl")
    _seed_channel(log)

    orch = Orchestrator(state_dir, config, transport)

    requested = orch.event_writer.emit(
        "channel.agent.reply.requested",
        actor="operator",
        payload=_reply_requested_payload(),
        correlation_id=CHANNEL_ID,
    )
    # Simulate the inline router path that already emitted started before
    # the reactor sees the requested event.
    orch.event_writer.emit(
        "channel.agent.reply.started",
        actor="orchestrator-reactor",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "request_id": REQUEST_ID,
            "message_id": MESSAGE_ID,
            "target_member_id": TARGET_MEMBER_ID,
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )

    orch._on_channel_agent_reply_requested(requested)

    started = [
        e for e in log.read_all() if e.type == "channel.agent.reply.started"
    ]
    same_request = [
        e for e in started if e.payload.get("request_id") == REQUEST_ID
    ]
    assert len(same_request) == 1, (
        "handler must not re-emit channel.agent.reply.started when one "
        f"already exists for (channel_id, request_id); got {len(same_request)}"
    )


# ---------------------------------------------------------------- test_c


def test_reply_requested_handler_skips_when_target_member_missing(
    state_dir: Path, config: ZfConfig, transport: TmuxTransport,
) -> None:
    """If the target member is not in the channel, dispatch_reply_request
    skips (status_pending check fails because the projection has no reply
    request entry for an unknown target). The handler must not raise."""
    log = EventLog(state_dir / "events.jsonl")
    _seed_channel(log, with_member=False)

    orch = Orchestrator(state_dir, config, transport)

    requested = orch.event_writer.emit(
        "channel.agent.reply.requested",
        actor="operator",
        payload=_reply_requested_payload(target="nonexistent"),
        correlation_id=CHANNEL_ID,
    )

    # Must not raise even though the target member is missing.
    decision = orch._on_channel_agent_reply_requested(requested)
    assert decision is None

    started = [
        e for e in log.read_all() if e.type == "channel.agent.reply.started"
    ]
    # The reply request itself exists in the projection (status=pending) so
    # dispatch_reply_request DOES emit started; but the member lookup returns
    # an empty dict and the backend resolves to "" → no fake path, no
    # headless path, no openclaw — just a no-op tail. The important
    # invariant: no exception, no infinite loop, no duplicate dispatch.
    same_request = [
        e for e in started if e.payload.get("request_id") == REQUEST_ID
    ]
    # Either 0 (skipped early) or 1 (started emitted, then dispatch tail
    # is a no-op because backend is empty and not in any known map). Both
    # are acceptable; what matters is no exception and no duplicates.
    assert len(same_request) <= 1
