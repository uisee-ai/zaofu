"""2026-07-03 audit B1: channel replies must not dead-end.

Before this batch, `channel.agent.reply.failed` had no consumer anywhere and
a dispatch that crashed after `started` was permanently blocked by the
started-event dedup. These tests cover the Tier-1 bounded redispatch, the
Tier-2 exhausted surfacing through the Run Manager, the generation-aware
reactor guard, and the orchestrator tick wiring.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.runtime.channel_reply_remediation import (
    CHANNEL_REPLY_EXHAUSTED_EVENT,
    channel_reply_remediation_candidates,
    pending_channel_reply_exhausted_actions,
    remediate_channel_replies,
)
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport

CH = "ch-remed"
REQ = "reply-req-1"
TARGET = "dev-1"
NOW = datetime.now(timezone.utc)


def _evt(etype: str, *, gen: int | None = None, age: float = 0.0,
         request_id: str = REQ, **extra) -> ZfEvent:
    payload = {
        "channel_id": CH,
        "thread_id": "main",
        "request_id": request_id,
        "message_id": "msg-1",
        "target_member_id": TARGET,
        **extra,
    }
    if gen is not None:
        payload["run_generation"] = gen
    return ZfEvent(
        type=etype,
        actor="test",
        payload=payload,
        correlation_id=CH,
        ts=(NOW - timedelta(seconds=age)).isoformat(),
    )


# ---------------------------------------------------------------- candidates


def test_failed_reply_is_immediate_redispatch_candidate():
    events = [
        _evt("channel.agent.reply.requested", age=1000),
        _evt("channel.agent.reply.started", age=990),
        _evt("channel.agent.reply.failed", age=980, reason="backend crashed"),
    ]
    cands = channel_reply_remediation_candidates(events, now=NOW)
    assert len(cands) == 1
    assert cands[0]["kind"] == "redispatch"
    assert cands[0]["status"] == "failed"
    assert cands[0]["run_generation"] == 1


def test_fresh_running_is_not_a_candidate_but_stale_running_is():
    fresh = [
        _evt("channel.agent.reply.requested", age=20),
        _evt("channel.agent.reply.started", age=10),
    ]
    assert channel_reply_remediation_candidates(fresh, now=NOW) == []
    stale = [
        _evt("channel.agent.reply.requested", age=2000),
        _evt("channel.agent.reply.started", age=1990),
    ]
    cands = channel_reply_remediation_candidates(stale, now=NOW)
    assert len(cands) == 1 and cands[0]["kind"] == "redispatch"


def test_completed_reply_is_never_a_candidate():
    events = [
        _evt("channel.agent.reply.requested", age=5000),
        _evt("channel.agent.reply.started", age=4990),
        _evt("channel.agent.reply.completed", age=4980),
    ]
    assert channel_reply_remediation_candidates(events, now=NOW) == []


def test_ancient_failure_is_history_not_work():
    # Enabling remediation on a long-lived ledger must not resurrect
    # failures that predate the feature.
    events = [
        _evt("channel.agent.reply.requested", age=3 * 86400 + 100),
        _evt("channel.agent.reply.failed", age=3 * 86400),
    ]
    assert channel_reply_remediation_candidates(events, now=NOW) == []


def test_stale_generation_events_do_not_mask_current_state():
    # gen-2 redispatch is running fresh; a late gen-1 failed must not
    # make the request look failed again (mirrors the projection rule).
    events = [
        _evt("channel.agent.reply.requested", age=2000),
        _evt("channel.agent.reply.started", gen=1, age=1990),
        _evt("channel.agent.reply.requested", gen=2, age=20),
        _evt("channel.agent.reply.started", gen=2, age=10),
        _evt("channel.agent.reply.failed", gen=1, age=5, reason="late zombie"),
    ]
    assert channel_reply_remediation_candidates(events, now=NOW) == []


# ---------------------------------------------------------------- remediate


def _seeded_log(tmp_path: Path, events: list[ZfEvent]) -> EventLog:
    log = EventLog(tmp_path / "events.jsonl")
    for event in events:
        log.append(event)
    return log


def test_remediate_redispatches_with_next_generation(tmp_path: Path):
    log = _seeded_log(tmp_path, [
        _evt("channel.agent.reply.requested", age=1000),
        _evt("channel.agent.reply.started", age=990),
        _evt("channel.agent.reply.failed", age=980, reason="crash"),
    ])
    writer = EventWriter(log)
    result = remediate_channel_replies(writer, events=log.read_all(), now=NOW)
    assert result["redispatched"] == [REQ]
    requested = [e for e in log.read_all()
                 if e.type == "channel.agent.reply.requested"
                 and e.payload.get("routing_reason") == "remediation_redispatch"]
    assert len(requested) == 1
    assert requested[0].payload["run_generation"] == 2
    assert requested[0].payload["target_member_id"] == TARGET
    # Second pass: the re-emitted request is fresh pending — no re-arm storm.
    again = remediate_channel_replies(writer, events=log.read_all(), now=NOW)
    assert again == {"redispatched": [], "exhausted": []}


def test_generation_cap_emits_exhausted_exactly_once(tmp_path: Path):
    log = _seeded_log(tmp_path, [
        _evt("channel.agent.reply.requested", gen=3, age=1000),
        _evt("channel.agent.reply.started", gen=3, age=990),
        _evt("channel.agent.reply.failed", gen=3, age=980, reason="still broken"),
    ])
    writer = EventWriter(log)
    result = remediate_channel_replies(writer, events=log.read_all(), now=NOW)
    assert result["exhausted"] == [REQ]
    again = remediate_channel_replies(writer, events=log.read_all(), now=NOW)
    assert again == {"redispatched": [], "exhausted": []}
    exhausted = [e for e in log.read_all() if e.type == CHANNEL_REPLY_EXHAUSTED_EVENT]
    assert len(exhausted) == 1
    assert exhausted[0].payload["run_generation"] == 3


# ------------------------------------------------- run-manager surfacing


def test_exhausted_surfaces_as_run_manager_pending_action(tmp_path: Path):
    from zf.runtime.run_manager import build_run_manager_projection

    events = [
        _evt("channel.agent.reply.requested", gen=3, age=1000),
        _evt("channel.agent.reply.failed", gen=3, age=980, reason="broken"),
        _evt(CHANNEL_REPLY_EXHAUSTED_EVENT, gen=3, age=900),
    ]
    actions = pending_channel_reply_exhausted_actions(events)
    assert len(actions) == 1
    action = actions[0]
    assert action["failure_class"] == "channel_reply_exhausted"
    assert action["action"] == "diagnose-attention"
    assert action["checkpoint_id"].startswith("channel-reply-exhausted-")

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    projection = build_run_manager_projection(state_dir, events=events)
    surfaced = [a for a in projection["pending_actions"]
                if a.get("failure_class") == "channel_reply_exhausted"]
    assert len(surfaced) == 1
    assert surfaced[0]["preflight"]["status"] == "passed"


def test_exhausted_action_clears_after_completion():
    events = [
        _evt("channel.agent.reply.requested", gen=3, age=1000),
        _evt("channel.agent.reply.failed", gen=3, age=980),
        _evt(CHANNEL_REPLY_EXHAUSTED_EVENT, gen=3, age=900),
        # Operator override redispatch eventually succeeded.
        _evt("channel.agent.reply.requested", gen=4, age=100),
        _evt("channel.agent.reply.completed", gen=4, age=50),
    ]
    assert pending_channel_reply_exhausted_actions(events) == []


# ---------------------------------------------- reactor + tick wiring


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def orch(state_dir: Path) -> Orchestrator:
    config = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )
    transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
    return Orchestrator(state_dir, config, transport)


def _seed_channel(log: EventLog) -> None:
    log.append(ZfEvent(
        type="channel.created", actor="web", correlation_id=CH,
        payload={"channel_id": CH, "name": "remed", "source": "web"},
    ))
    log.append(ZfEvent(
        type="channel.member.added", actor="web", correlation_id=CH,
        payload={
            "channel_id": CH, "thread_id": "main", "member_id": TARGET,
            "persona": "Dev", "member_type": "persona_agent",
            "provider": "claude-code", "backend": "claude-code",
            "permissions": ["read", "message"], "source": "web",
        },
    ))
    log.append(ZfEvent(
        type="channel.message.posted", actor="operator", correlation_id=CH,
        payload={
            "channel_id": CH, "thread_id": "main", "message_id": "msg-1",
            "member_id": "operator", "role": "user",
            "text": f"@{TARGET} please review", "source": "web",
        },
    ))


def test_reactor_guard_allows_higher_generation_redispatch(
    state_dir: Path, orch: Orchestrator,
) -> None:
    log = EventLog(state_dir / "events.jsonl")
    _seed_channel(log)
    # gen-1 crashed after started: started exists, no terminal event.
    log.append(_evt("channel.agent.reply.requested", status="pending",
                    member_type="persona_agent", backend="claude-code"))
    log.append(_evt("channel.agent.reply.started", gen=1))

    requested_gen2 = orch.event_writer.emit(
        "channel.agent.reply.requested",
        actor="orchestrator-remediation",
        correlation_id=CH,
        payload={
            "channel_id": CH, "thread_id": "main", "request_id": REQ,
            "message_id": "msg-1", "target_member_id": TARGET,
            "status": "pending", "run_generation": 2,
            "routing_reason": "remediation_redispatch", "source": "runtime",
        },
    )
    orch._on_channel_agent_reply_requested(requested_gen2)
    started = [e for e in log.read_all()
               if e.type == "channel.agent.reply.started"
               and e.payload.get("request_id") == REQ]
    assert len(started) == 2, "gen-2 redispatch must not be blocked by gen-1 started"
    assert any(int(e.payload.get("run_generation") or 1) == 2 for e in started)


def test_reactor_guard_still_dedups_same_generation(
    state_dir: Path, orch: Orchestrator,
) -> None:
    log = EventLog(state_dir / "events.jsonl")
    _seed_channel(log)
    requested = orch.event_writer.emit(
        "channel.agent.reply.requested",
        actor="operator", correlation_id=CH,
        payload={
            "channel_id": CH, "thread_id": "main", "request_id": REQ,
            "message_id": "msg-1", "target_member_id": TARGET,
            "status": "pending", "source": "web",
        },
    )
    orch.event_writer.emit(
        "channel.agent.reply.started",
        actor="orchestrator-reactor", correlation_id=CH,
        payload={"channel_id": CH, "thread_id": "main", "request_id": REQ,
                 "message_id": "msg-1", "target_member_id": TARGET,
                 "source": "runtime"},
    )
    orch._on_channel_agent_reply_requested(requested)
    started = [e for e in log.read_all()
               if e.type == "channel.agent.reply.started"
               and e.payload.get("request_id") == REQ]
    assert len(started) == 1


def test_tick_housekeeping_self_heals_failed_reply(
    state_dir: Path, orch: Orchestrator,
) -> None:
    """End-to-end Tier-1: a failed reply on the ledger + one housekeeping
    tick → remediation re-emits requested (gen 2) and the immediate
    dispatch runs the persona fake path to completion."""
    log = EventLog(state_dir / "events.jsonl")
    _seed_channel(log)
    log.append(_evt("channel.agent.reply.requested", age=1000, status="pending",
                    member_type="persona_agent", backend="claude-code"))
    log.append(_evt("channel.agent.reply.started", age=990))
    log.append(_evt("channel.agent.reply.failed", age=980, reason="crash"))

    orch._check_channel_reply_remediation()

    events = log.read_all()
    redispatched = [e for e in events
                    if e.type == "channel.agent.reply.requested"
                    and e.payload.get("routing_reason") == "remediation_redispatch"]
    assert len(redispatched) == 1
    assert redispatched[0].payload["run_generation"] == 2
    completed = [e for e in events
                 if e.type == "channel.agent.reply.completed"
                 and e.payload.get("request_id") == REQ]
    assert completed, "persona fake dispatch should complete the redispatched reply"


def test_tick_registration_present_in_run_once() -> None:
    import inspect

    from zf.runtime import orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert 'channel_reply_remediation' in source, (
        "run_once housekeeping must register _check_channel_reply_remediation"
    )
    assert hasattr(Orchestrator, "_check_channel_reply_remediation")
