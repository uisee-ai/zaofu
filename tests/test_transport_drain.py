"""Tests for Orchestrator draining transport.poll_events (G-EVT-2)."""

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
def config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )


class _TransportWithPendingEvents(TmuxTransport):
    """Transport that yields agent.* events from poll_events() like the
    real StreamJsonTransport would after a send_task round trip."""

    def __init__(self):
        super().__init__(TmuxSession(session_name="t", dry_run=True))
        self._queued: list[ZfEvent] = []

    def enqueue(self, *events: ZfEvent) -> None:
        self._queued.extend(events)

    def poll_events(self) -> list[ZfEvent]:
        out = list(self._queued)
        self._queued.clear()
        return out


class TestRunOnceDrainsTransport:
    def test_pending_events_land_in_events_jsonl(self, state_dir, config):
        transport = _TransportWithPendingEvents()
        transport.enqueue(
            ZfEvent(
                type="agent.tool.use",
                actor="dev",
                payload={"tool": "Read", "input": {"path": "x.py"}},
            ),
            ZfEvent(
                type="agent.usage",
                actor="dev",
                payload={"usage": {"input_tokens": 100, "output_tokens": 50}},
            ),
        )
        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        log = EventLog(state_dir / "events.jsonl")
        types = [e.type for e in log.read_all()]
        assert "agent.tool.use" in types
        assert "agent.usage" in types

    def test_drained_events_fire_housekeeping(self, state_dir, config):
        """Draining agent.usage should also run _apply_housekeeping so
        CostTracker records the usage."""
        transport = _TransportWithPendingEvents()
        transport.enqueue(
            ZfEvent(
                type="agent.usage",
                actor="dev",
                payload={"usage": {"input_tokens": 1500, "output_tokens": 200}},
            ),
        )
        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        # CostTracker should have picked it up
        totals = orch.cost_tracker.per_role_totals()
        assert "dev" in totals
        assert totals["dev"].input_tokens == 1500

    def test_empty_queue_is_noop(self, state_dir, config):
        transport = _TransportWithPendingEvents()
        # No events queued
        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()
        assert isinstance(decisions, list)

    def test_drain_happens_after_dispatch_so_tool_uses_captured(
        self, state_dir, config
    ):
        """Drain should happen at the end of run_once so any events
        produced during dispatch are captured in the same cycle."""
        transport = _TransportWithPendingEvents()
        # Events that would have been produced by a Layer 2 dispatch
        transport.enqueue(
            ZfEvent(type="agent.text", actor="orchestrator", payload={"text": "hi"}),
        )
        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        log = EventLog(state_dir / "events.jsonl")
        types = [e.type for e in log.read_all()]
        assert "agent.text" in types


class TestLayer2Cooldown:
    """B11: when transport drains agent.api_blocked or agent.timeout,
    Layer 2 dispatch enters a cool-down to avoid wake-storm against
    a rate-limited Claude API."""

    def _layer2_config(self):
        # Config WITH orchestrator role so layer2_active path is taken
        return ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(name="orchestrator", backend="claude-code",
                           transport="stream-json",
                           triggers=["user.message"]),
                RoleConfig(name="dev", backend="mock"),
            ],
        )

    def test_api_blocked_event_arms_cooldown(self, state_dir):
        config = self._layer2_config()
        transport = _TransportWithPendingEvents()
        transport.enqueue(
            ZfEvent(type="agent.api_blocked", actor="orchestrator",
                    payload={"reason": "rate_limit"}),
        )
        orch = Orchestrator(state_dir, config, transport)
        # Cool-down starts at 0 (no block yet)
        assert orch._layer2_blocked_until == 0.0

        orch.run_once()

        import time
        # Cool-down armed for ~rate_limit_cooldown_s (default 60s)
        assert orch._layer2_blocked_until > time.time()
        assert orch._layer2_blocked_until <= time.time() + 61

    def test_dispatch_skipped_during_cooldown(self, state_dir):
        """While cool-down is active, _notify_orchestrator_agent should
        emit orchestrator.dispatch_skipped instead of calling send_task."""
        config = self._layer2_config()
        transport = _TransportWithPendingEvents()
        orch = Orchestrator(state_dir, config, transport)

        # Manually arm cool-down
        import time
        orch._layer2_blocked_until = time.time() + 30

        # Push a user.message event — should NOT dispatch to Layer 2
        trigger = ZfEvent(type="user.message", actor="human",
                         payload={"text": "hi"})
        orch.event_log.append(trigger)
        orch.run_once(events=[trigger])

        log = EventLog(state_dir / "events.jsonl")
        types = [e.type for e in log.read_all()]
        assert "orchestrator.dispatch_skipped" in types
        # send_task NOT invoked → no agent.* events from transport poll
        skipped = next(
            e for e in log.read_all()
            if e.type == "orchestrator.dispatch_skipped"
        )
        assert skipped.payload["reason"] == "layer2_cooldown"

    def test_dispatch_proceeds_after_cooldown_expires(self, state_dir):
        config = self._layer2_config()
        transport = _TransportWithPendingEvents()
        orch = Orchestrator(state_dir, config, transport)

        # Arm cool-down, then expire it
        import time
        orch._layer2_blocked_until = time.time() - 10  # already past

        # Should NOT skip
        trigger = ZfEvent(type="user.message", actor="human",
                         payload={"text": "hi"})
        orch.event_log.append(trigger)
        # The actual send_task will likely fail (no real claude), but the
        # dispatch_skipped event should NOT fire
        try:
            orch.run_once(events=[trigger])
        except Exception:
            pass

        log = EventLog(state_dir / "events.jsonl")
        types = [e.type for e in log.read_all()]
        assert "orchestrator.dispatch_skipped" not in types
