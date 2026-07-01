"""Tests for G-RECYCLE-8: _check_context_thresholds feeds cost tracker.

After reading usage from a session file for the recycle decision, the
orchestrator should also synthesise an agent.usage event so tmux-hosted
workers (which have no SDK path producing these events) land data in
CostTracker. Dedup prevents double-counting when both paths fire.
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


@pytest.fixture
def config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", backend="claude-code"),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


class _FakeReader:
    def __init__(self, reports: list[UsageReport]):
        self._reports = list(reports)
        self._idx = 0
        self.session_path_calls = 0

    def session_path(self, *a, **kw):
        self.session_path_calls += 1
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


class TestAgentUsageSynthesized:
    def test_threshold_check_emits_agent_usage_from_disk(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._session_readers = {
            "claude-code": _FakeReader([_usage(0.3, "2026-04-15T10:00:00Z")]),
        }
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")

        orch._check_context_thresholds()

        events = EventLog(state_dir / "events.jsonl").read_all()
        agent_usage_events = [
            e for e in events if e.type == "agent.usage"
        ]
        # At least one synthesised agent.usage event
        assert len(agent_usage_events) >= 1
        # And its payload carries the disk source tag
        assert any(
            e.payload.get("source") == "disk_reader"
            for e in agent_usage_events
        )

    def test_cost_tracker_receives_tokens_from_disk_reader(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._session_readers = {
            "claude-code": _FakeReader([_usage(0.3, "2026-04-15T10:00:00Z")]),
        }
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")

        orch._check_context_thresholds()

        totals = orch.cost_tracker.per_role_totals()
        # Dev should have a cost entry now
        assert "dev" in totals
        assert totals["dev"].input_tokens > 0


class TestDedupe:
    def test_same_timestamp_twice_counted_once(
        self, state_dir, config, transport
    ):
        """Calling _check_context_thresholds twice with identical
        session usage (same timestamp) must not double-count tokens."""
        fixed = _usage(0.3, "2026-04-15T10:00:00Z")
        orch = Orchestrator(state_dir, config, transport)
        orch._session_readers = {"claude-code": _FakeReader([fixed, fixed])}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")

        orch._check_context_thresholds()
        first = orch.cost_tracker.per_role_totals()["dev"].input_tokens

        orch._check_context_thresholds()
        second = orch.cost_tracker.per_role_totals()["dev"].input_tokens
        assert first == second  # no double count

    def test_new_timestamp_is_counted(
        self, state_dir, config, transport
    ):
        """Different timestamp → new usage → add to totals."""
        orch = Orchestrator(state_dir, config, transport)
        orch._session_readers = {
            "claude-code": _FakeReader([
                _usage(0.3, "2026-04-15T10:00:00Z"),
                _usage(0.4, "2026-04-15T10:01:00Z"),
            ]),
        }
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")

        orch._check_context_thresholds()
        first = orch.cost_tracker.per_role_totals()["dev"].input_tokens

        orch._check_context_thresholds()
        second = orch.cost_tracker.per_role_totals()["dev"].input_tokens
        assert second > first


class TestCodexUnboundSessions:
    def test_unbound_codex_role_does_not_read_global_project_session(
        self, state_dir, transport
    ):
        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", backend="codex")],
        )
        reader = _FakeReader([_usage(0.7, "2026-04-15T10:00:00Z")])
        orch = Orchestrator(state_dir, config, transport)
        orch._session_readers = {"codex": reader}

        orch._check_context_thresholds()

        assert reader.session_path_calls == 0
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert [e for e in events if e.type == "agent.usage"] == []

    def test_bound_codex_role_still_reports_usage(
        self, state_dir, transport, tmp_path
    ):
        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", backend="codex")],
        )
        reader = _FakeReader([_usage(0.3, "2026-04-15T10:00:00Z")])
        orch = Orchestrator(state_dir, config, transport)
        orch._session_readers = {"codex": reader}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).bind_codex_session(
            "dev",
            "019e1f2a-f40b-7a61-8cfa-c73ae0af4eb2",
            session_path=tmp_path / "rollout.jsonl",
        )

        orch._check_context_thresholds()

        assert reader.session_path_calls == 1
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(e.type == "agent.usage" for e in events)
