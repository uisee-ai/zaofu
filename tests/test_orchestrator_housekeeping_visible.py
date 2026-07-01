"""ZF-HOUSEKEEPING-VISIBLE-001 (doc 42 §2.12) — observable housekeeping failures.

orchestrator.run_once previously had 8 ``try: fn() except Exception: pass``
blocks that silently swallowed drift / progress / orphan / refresh / recycle
errors. This sprint replaces them with ``_safe_housekeeping(step, fn)`` which
catches the same exceptions but emits ``kernel.housekeeping.failed`` with
60 s per-step dedup. These tests assert:

1. A raising housekeeping step produces a kernel.housekeeping.failed event
2. The event payload includes step / exc_type / exc_repr
3. Dedup keeps event count bounded under repeated failures of the same step
4. Different steps do NOT dedup each other (per-step isolation)
5. run_once never raises even when housekeeping crashes
6. event_writer crashing inside the helper still doesn't break the loop
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

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
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def legacy_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", backend="mock"),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


@pytest.fixture
def orchestrator(state_dir, legacy_config, transport):
    return Orchestrator(state_dir, legacy_config, transport)


def _read_events(state_dir: Path) -> list[dict]:
    """Return all events from events.jsonl as parsed dicts."""
    path = state_dir / "events.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


class TestSafeHousekeepingBasics:
    def test_helper_exists(self, orchestrator):
        """Wire-up grep proof: _safe_housekeeping is an instance method."""
        assert callable(getattr(orchestrator, "_safe_housekeeping", None))
        assert callable(getattr(orchestrator, "_emit_housekeeping_failure", None))

    def test_dedup_state_initialized(self, orchestrator):
        """__init__ created the dedup state."""
        assert orchestrator._housekeeping_failure_last == {}
        assert orchestrator._housekeeping_failure_dedup_seconds == 60.0

    def test_successful_step_no_event(self, orchestrator, state_dir):
        """A successful housekeeping step emits no failure event."""
        before = len(_read_events(state_dir))
        orchestrator._safe_housekeeping("noop", lambda: None)
        after = _read_events(state_dir)
        assert len(after) == before  # no new event


class TestFailureEmitsEvent:
    def test_raising_step_emits_kernel_housekeeping_failed(
        self, orchestrator, state_dir
    ):
        def bad():
            raise RuntimeError("simulated drift failure")

        orchestrator._safe_housekeeping("drift", bad)

        events = _read_events(state_dir)
        failure_events = [e for e in events if e["type"] == "kernel.housekeeping.failed"]
        assert len(failure_events) == 1
        e = failure_events[0]
        assert e["actor"] == "orchestrator"
        assert e["payload"]["step"] == "drift"
        assert e["payload"]["exc_type"] == "RuntimeError"
        assert "simulated drift failure" in e["payload"]["exc_repr"]

    def test_exc_repr_truncated_at_500_chars(self, orchestrator, state_dir):
        long_msg = "x" * 2000

        def bad():
            raise RuntimeError(long_msg)

        orchestrator._safe_housekeeping("drift", bad)

        events = _read_events(state_dir)
        failure_events = [e for e in events if e["type"] == "kernel.housekeeping.failed"]
        assert len(failure_events) == 1
        # repr() adds RuntimeError(...) wrapper so total can be slightly under 500
        assert len(failure_events[0]["payload"]["exc_repr"]) <= 500


class TestDedup:
    def test_repeated_same_step_dedups(self, orchestrator, state_dir):
        """100 failures of same step inside the dedup window → 1 event."""
        def bad():
            raise ValueError("drift broke")

        for _ in range(100):
            orchestrator._safe_housekeeping("drift", bad)

        events = _read_events(state_dir)
        failure_events = [e for e in events if e["type"] == "kernel.housekeeping.failed"]
        assert len(failure_events) == 1

    def test_different_steps_do_not_dedup_each_other(
        self, orchestrator, state_dir
    ):
        """Per-step isolation: drift and orphaned_tasks both fire → 2 events."""
        orchestrator._safe_housekeeping(
            "drift", lambda: (_ for _ in ()).throw(RuntimeError("d"))
        )
        orchestrator._safe_housekeeping(
            "orphaned_tasks",
            lambda: (_ for _ in ()).throw(KeyError("o")),
        )
        orchestrator._safe_housekeeping(
            "regenerate_progress",
            lambda: (_ for _ in ()).throw(OSError("p")),
        )

        events = _read_events(state_dir)
        failure_events = [e for e in events if e["type"] == "kernel.housekeeping.failed"]
        assert len(failure_events) == 3
        steps = sorted(e["payload"]["step"] for e in failure_events)
        assert steps == ["drift", "orphaned_tasks", "regenerate_progress"]

    def test_dedup_window_resets(self, orchestrator, state_dir, monkeypatch):
        """After the dedup window passes, a second failure emits a new event."""
        import zf.runtime.orchestrator as orch_mod

        # First emit at t=1000
        monkeypatch.setattr(orch_mod.time, "time", lambda: 1000.0)
        orchestrator._safe_housekeeping(
            "drift", lambda: (_ for _ in ()).throw(RuntimeError("first"))
        )

        # Second emit at t=1030 (within 60 s window) → should dedup
        monkeypatch.setattr(orch_mod.time, "time", lambda: 1030.0)
        orchestrator._safe_housekeeping(
            "drift", lambda: (_ for _ in ()).throw(RuntimeError("second"))
        )

        # Third emit at t=1100 (past 60 s window) → should emit
        monkeypatch.setattr(orch_mod.time, "time", lambda: 1100.0)
        orchestrator._safe_housekeeping(
            "drift", lambda: (_ for _ in ()).throw(RuntimeError("third"))
        )

        events = _read_events(state_dir)
        failure_events = [e for e in events if e["type"] == "kernel.housekeeping.failed"]
        assert len(failure_events) == 2
        reprs = [e["payload"]["exc_repr"] for e in failure_events]
        assert any("first" in r for r in reprs)
        assert any("third" in r for r in reprs)
        # "second" was deduped, must NOT appear
        assert not any("second" in r for r in reprs)


class TestLoopIntegrity:
    def test_helper_does_not_reraise(self, orchestrator):
        """_safe_housekeeping must never let exceptions escape — that's
        the whole contract (I6 — loop must continue)."""
        try:
            orchestrator._safe_housekeeping(
                "drift", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
            )
        except Exception as exc:
            pytest.fail(f"_safe_housekeeping leaked exception: {exc!r}")

    def test_emit_failure_with_broken_event_writer_falls_back_to_stderr(
        self, orchestrator, capsys
    ):
        """If event_writer.append itself raises, the helper must NOT
        re-raise. Last-line defence: stderr message."""
        class BrokenWriter:
            def append(self, event):
                raise IOError("disk full")

        orchestrator.event_writer = BrokenWriter()

        try:
            orchestrator._safe_housekeeping(
                "drift", lambda: (_ for _ in ()).throw(RuntimeError("primary"))
            )
        except Exception as exc:
            pytest.fail(f"helper leaked exception with broken writer: {exc!r}")

        captured = capsys.readouterr()
        assert "kernel.housekeeping.failed" in captured.err
        assert "drift" in captured.err

    def test_run_once_continues_when_all_housekeeping_fails(
        self, orchestrator, state_dir, monkeypatch
    ):
        """All 6 housekeeping checks raise → run_once still returns
        decisions list and emits 6 distinct failure events."""
        # Monkeypatch each housekeeping target to raise distinct exception
        def make_thrower(label):
            def _raise():
                raise RuntimeError(f"failure-in-{label}")
            return _raise

        monkeypatch.setattr(
            orchestrator,
            "_check_context_thresholds",
            make_thrower("context_thresholds"),
        )
        monkeypatch.setattr(
            orchestrator, "_check_pending_recycles", make_thrower("pending_recycles")
        )
        monkeypatch.setattr(
            orchestrator, "_check_orphaned_tasks", make_thrower("orphaned_tasks")
        )
        monkeypatch.setattr(
            orchestrator, "_check_fanout_timeouts", make_thrower("fanout_timeouts")
        )
        monkeypatch.setattr(orchestrator, "_check_drift", make_thrower("drift"))
        monkeypatch.setattr(
            orchestrator,
            "_check_refresh_triggers",
            make_thrower("refresh"),
        )
        # Don't sabotage _emit_decision_recorded — that one's separately tested
        # and we want a clean baseline emit at the tail of run_once.

        try:
            result = orchestrator.run_once(events=[])
        except Exception as exc:
            pytest.fail(f"run_once leaked exception: {exc!r}")

        assert isinstance(result, list)

        events = _read_events(state_dir)
        failure_events = [
            e for e in events if e["type"] == "kernel.housekeeping.failed"
        ]
        steps = sorted(e["payload"]["step"] for e in failure_events)
        # 6 housekeeping steps wrapped in run_once (excluding regenerate_progress
        # which can also fail but is OS-dependent in tests, and
        # decision_recorded which is separately wrapped)
        assert "drift" in steps
        assert "orphaned_tasks" in steps
        assert "fanout_timeouts" in steps
        assert "refresh" in steps
        assert "context_thresholds" in steps
        assert "pending_recycles" in steps


class TestRunOnceIntegration:
    def test_run_once_emits_no_housekeeping_failure_on_clean_run(
        self, orchestrator, state_dir
    ):
        """Healthy run_once should NOT emit kernel.housekeeping.failed."""
        orchestrator.run_once(events=[])
        events = _read_events(state_dir)
        failure_events = [
            e for e in events if e["type"] == "kernel.housekeeping.failed"
        ]
        assert failure_events == []
