"""LH-0.T4: context-ratio hard cap.

When a worker's context ratio hits ``recycle_hard_cap`` (default 0.9),
Layer 1 marks the instance in ``_hard_cap_exceeded`` so:
  - ``_find_available_role`` stops dispatching to it (natural drain)
  - if it stays busy past ``drain_hold_seconds`` (default 180), force
    a recycle regardless of busy/idle (previously pending_recycle only
    advanced when the worker happened to go idle — which for busy
    workers on long tasks never happens, letting context grow until
    the session crashes)

Mark is cleared when the ratio drops back under ``recycle_threshold``
or when a ``worker.recycled`` event is observed for that instance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig, RoleConfig, SessionConfig, ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
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

    def session_path(self, *a, **kw):
        return Path("/tmp/fake")

    def read_latest_usage(self, *a, **kw):
        if self._idx >= len(self._reports):
            return self._reports[-1] if self._reports else None
        r = self._reports[self._idx]
        self._idx += 1
        return r


def _usage(ratio: float, ts: str = "2026-04-19T10:00:00Z",
           window: int = 200_000) -> UsageReport:
    return UsageReport(
        effective_input_tokens=int(ratio * window),
        output_tokens=100,
        model_context_window=window,
        ratio=ratio,
        timestamp=ts,
        raw={"input_tokens": int(ratio * window), "output_tokens": 100,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    )


def _prep_worker(state_dir: Path, instance_id: str = "dev") -> None:
    RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(state_dir.parent),
    ).get_or_create(instance_id)


def _assign_busy(state_dir: Path, instance_id: str = "dev") -> None:
    TaskStore(state_dir / "kanban.json").add(Task(
        title="t", status="in_progress", assigned_to=instance_id,
    ))


class TestConfigSchema:
    def test_drain_hold_default(self):
        assert RoleConfig(name="dev").drain_hold_seconds == 180.0


class TestHardCapMarks:
    def test_ratio_at_hard_cap_marks_instance(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        _prep_worker(state_dir)
        _assign_busy(state_dir)
        orch._session_readers = {
            "claude-code": _FakeReader([_usage(0.95)]),
        }
        orch._now = lambda: 100.0
        orch._check_context_thresholds()
        assert "dev" in orch._hard_cap_exceeded
        assert orch._hard_cap_exceeded["dev"] == 100.0

    def test_below_threshold_does_not_mark(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        _prep_worker(state_dir)
        orch._session_readers = {
            "claude-code": _FakeReader([_usage(0.3)]),
        }
        orch._check_context_thresholds()
        assert "dev" not in orch._hard_cap_exceeded


class TestDispatchBlocked:
    def test_find_available_role_skips_hard_capped_instance(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._hard_cap_exceeded["dev"] = 100.0

        task = Task(id="T1", title="x", status="backlog", assigned_to="dev")
        role = orch._find_available_role(task)
        assert role is None, "hard-cap-exceeded instance must not be chosen"


class TestForceRecycleAfterDrainHold:
    def test_busy_past_drain_hold_forces_recycle(
        self, state_dir, config, transport
    ):
        """Busy worker stuck over hard cap past drain_hold_seconds:
        force _start_recycle so context stops growing."""
        orch = Orchestrator(state_dir, config, transport)
        _prep_worker(state_dir)
        _assign_busy(state_dir)  # worker is busy
        orch._session_readers = {
            "claude-code": _FakeReader([_usage(0.95), _usage(0.95)]),
        }
        # Cycle 1: mark at t=0
        orch._now = lambda: 0.0
        recycled = []
        orch._start_recycle = lambda role: recycled.append(role.instance_id)
        orch._check_context_thresholds()
        assert recycled == []  # within drain_hold, no force

        # Cycle 2: t=200 > drain_hold=180 → force
        orch._now = lambda: 200.0
        orch._check_context_thresholds()
        assert recycled == ["dev"]

    def test_within_drain_hold_no_force(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        _prep_worker(state_dir)
        _assign_busy(state_dir)
        orch._session_readers = {
            "claude-code": _FakeReader([_usage(0.95), _usage(0.95)]),
        }
        orch._now = lambda: 0.0
        recycled = []
        orch._start_recycle = lambda role: recycled.append(role.instance_id)
        orch._check_context_thresholds()

        orch._now = lambda: 100.0  # under 180
        orch._check_context_thresholds()
        assert recycled == []


class TestMarkClearance:
    def test_recycled_event_clears_mark(
        self, state_dir, config, transport
    ):
        orch = Orchestrator(state_dir, config, transport)
        orch._hard_cap_exceeded["dev"] = 50.0

        # Observing worker.recycled event must clear the mark.
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="worker.recycled", actor="dev", payload={},
        ))
        orch.run_once()
        assert "dev" not in orch._hard_cap_exceeded


class TestWireUpProof:
    def test_hard_cap_exceeded_used_in_dispatch(self):
        # K1 切片 3 后,dispatch 的只读查询住 mixin 文件;wire-up 实质
        # (DispatchMixin 经继承消费 _hard_cap_exceeded)不变,grep 域
        # 取三文件并集。
        root = Path(__file__).resolve().parents[1] / "src/zf/runtime"
        src = "".join(
            (root / name).read_text()
            for name in (
                "orchestrator_dispatch.py",
                "dispatch_evidence_queries.py",
                "dispatch_routing_queries.py",
            )
        )
        assert "_hard_cap_exceeded" in src
