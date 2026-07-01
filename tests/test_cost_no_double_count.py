"""Fix E1 — cost.jsonl must not double-count agent.usage events.

Run 15 post-mortem: cost.jsonl had 16 entries for 8 agent.usage events
in events.jsonl. Root cause: both _synthesize_agent_usage and
_drain_transport_events called _apply_housekeeping inline after appending
to the log, and then _react_to_events on the NEXT cycle read the same
event from offset and housekept it again.

Fix: the two inline sites add event.id to _processed_event_ids so the
_react_to_events loop's own dedup (already present at line 368) covers
the second path.

Also covers Finding 3: orchestrator role now feeds cost tracker. Before
the fix, _check_context_thresholds hard-skipped role.name=='orchestrator'
so Layer 2's LLM spend was invisible.
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
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.cost.tracker import CostTracker
from zf.runtime.backend_session_reader import UsageReport
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


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


class _TransportWithPendingEvents(TmuxTransport):
    def __init__(self):
        super().__init__(TmuxSession(session_name="t", dry_run=True))
        self._queued: list[ZfEvent] = []

    def enqueue(self, *events: ZfEvent) -> None:
        self._queued.extend(events)

    def poll_events(self) -> list[ZfEvent]:
        out = list(self._queued)
        self._queued.clear()
        return out


class _FakeReader:
    def __init__(self, reports: list[UsageReport]):
        self._reports = list(reports)
        self._idx = 0

    def session_path(self, *a, **kw):
        return Path("/tmp/fake")

    def read_latest_usage(self, *a, **kw):
        if self._idx >= len(self._reports):
            return self._reports[-1] if self._reports else None
        r = self._reports[self._idx]
        self._idx += 1
        return r


def _usage(ratio: float, timestamp: str, window: int = 200_000) -> UsageReport:
    return UsageReport(
        effective_input_tokens=int(ratio * window),
        output_tokens=100,
        model_context_window=window,
        ratio=ratio,
        timestamp=timestamp,
        raw={
            "input_tokens": int(ratio * window),
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "output_tokens": 100,
        },
    )


class TestDrainNoDoubleCount:
    """_drain_transport_events appends + housekeeps inline; on the next
    run_once cycle _react_to_events must NOT housekeep the same event."""

    def test_single_drain_cost_equals_one_entry(self, state_dir):
        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        transport = _TransportWithPendingEvents()
        transport.enqueue(
            ZfEvent(
                type="agent.usage",
                actor="dev",
                payload={"usage": {"input_tokens": 1000, "output_tokens": 200}},
            ),
        )
        orch = Orchestrator(state_dir, config, transport)

        # Cycle 1: drain inline-housekeeps.
        orch.run_once()
        after_1 = orch.cost_tracker.per_role_totals()["dev"].input_tokens

        # Cycle 2: nothing new; _react_to_events reads the drained event
        # from offset but must skip housekeeping (already _processed).
        orch.run_once()
        after_2 = orch.cost_tracker.per_role_totals()["dev"].input_tokens

        assert after_1 == 1000
        assert after_2 == 1000, "E1 regression: drain-path double-counted"


class TestSynthesizeNoDoubleCount:
    """_synthesize_agent_usage appends + housekeeps inline; the next
    _react_to_events pass must skip the duplicate."""

    def test_synth_plus_react_cost_equals_one_entry(self, state_dir):
        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", backend="claude-code")],
        )
        transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
        orch = Orchestrator(state_dir, config, transport)
        orch._session_readers = {
            "claude-code": _FakeReader([_usage(0.3, "2026-04-18T10:00:00Z")]),
        }
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")

        # Trigger synth + inline housekeeping.
        orch._check_context_thresholds()
        after_synth = orch.cost_tracker.per_role_totals()["dev"].input_tokens
        entries_synth = orch.cost_tracker.per_role_totals()["dev"].entries

        # Now run_once: _react_to_events reads the synthesized agent.usage
        # event from offset; it must NOT record cost a second time.
        orch.run_once()
        after_run = orch.cost_tracker.per_role_totals()["dev"].input_tokens
        entries_run = orch.cost_tracker.per_role_totals()["dev"].entries

        assert after_run == after_synth, (
            f"E1 regression: synth-path double-counted "
            f"(tokens {after_synth} → {after_run})"
        )
        assert entries_run == entries_synth == 1

    def test_rebuild_dedupes_repeated_disk_reader_snapshot(self, tmp_path: Path):
        """R37 regression: a watcher can emit the same disk-reader usage
        snapshot many times with different event ids. Rebuilding cost from
        events must count the snapshot once."""
        payload = {
            "usage": {"input_tokens": 1000, "output_tokens": 200},
            "source": "disk_reader",
            "backend": "codex",
            "model": "default",
            "model_context_window": 200_000,
            "usage_timestamp": "2026-06-21T10:00:00Z",
        }
        events = [
            ZfEvent(type="agent.usage", id="evt-a", actor="dev-1", payload=payload),
            ZfEvent(type="agent.usage", id="evt-b", actor="dev-1", payload=payload),
        ]

        tracker = CostTracker.rebuild_from_events(events, tmp_path / "cost.jsonl")

        totals = tracker.per_instance_totals()
        assert totals["dev-1"].entries == 1
        assert totals["dev-1"].input_tokens == 1000


class TestOrchestratorCostTracked:
    """E1 Finding 3: orchestrator's own LLM spend must land in cost.jsonl.

    Previously _check_context_thresholds hard-skipped role.name ==
    'orchestrator' at the top of the loop, so Layer 2 was a cost blind
    spot. After the fix, orchestrator usage is synthesized like any
    other role; only the recycle DECISION is skipped (can't hot-swap
    Layer 2 mid-flight).
    """

    def test_orchestrator_usage_feeds_cost_tracker(self, state_dir):
        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(name="orchestrator", backend="claude-code"),
                RoleConfig(name="dev", backend="claude-code"),
            ],
        )
        transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
        orch = Orchestrator(state_dir, config, transport)
        orch._session_readers = {
            "claude-code": _FakeReader([_usage(0.4, "2026-04-18T10:00:00Z")]),
        }
        reg = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        reg.get_or_create("orchestrator")
        reg.get_or_create("dev")

        orch._check_context_thresholds()

        totals = orch.cost_tracker.per_role_totals()
        assert "orchestrator" in totals, (
            "E1 regression: orchestrator cost not tracked — Layer 2 is "
            "a blind spot"
        )
        assert totals["orchestrator"].input_tokens > 0

    def test_orchestrator_high_ratio_does_not_trigger_recycle(self, state_dir):
        """Orchestrator hitting recycle_threshold must NOT emit
        worker.context.warning or flip instance_state — Layer 2's
        session cannot be hot-swapped."""
        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="orchestrator",
                    backend="claude-code",
                    recycle_threshold=0.5,
                ),
            ],
        )
        transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
        orch = Orchestrator(state_dir, config, transport)
        # ratio=0.9 would trigger recycle for any worker; for orchestrator
        # it should only feed cost.
        orch._session_readers = {
            "claude-code": _FakeReader([_usage(0.9, "2026-04-18T10:00:00Z")]),
        }
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("orchestrator")

        orch._check_context_thresholds()

        events = EventLog(state_dir / "events.jsonl").read_all()
        warning_types = {"worker.context.warning", "worker.context.critical"}
        has_warning = any(
            e.type in warning_types and e.actor == "orchestrator"
            for e in events
        )
        assert not has_warning, (
            "orchestrator must not emit recycle warnings — Layer 2 "
            "can't be hot-swapped"
        )
        assert "orchestrator" not in orch._instance_state
