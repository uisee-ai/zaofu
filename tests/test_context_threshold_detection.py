"""Tests for G-RECYCLE-4 + 5: _check_context_thresholds and
_check_pending_recycles.

Threshold detection reads per-instance usage via BackendSessionReader
and flips instance state to pending_recycle or recycling based on
idle status. Pending recycle drain advances pending_recycle →
recycling once the instance is truly idle (no in_progress task).
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
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.backend_session_reader import UsageReport
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.transport import TmuxTransport
from zf.runtime.tmux import TmuxSession


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
def claude_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(
                name="dev",
                backend="claude-code",
                recycle_threshold=0.5,
                recycle_hard_cap=0.9,
            ),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def _make_usage(ratio: float, window: int = 200_000) -> UsageReport:
    return UsageReport(
        effective_input_tokens=int(ratio * window),
        output_tokens=100,
        model_context_window=window,
        ratio=ratio,
        timestamp="2026-04-15T10:00:00Z",
        raw={"input_tokens": int(ratio * window)},
    )


class _FakeReader:
    def __init__(self, report: UsageReport | None):
        self._report = report

    def session_path(self, project_root, session_id, *, cached_path=None):
        return Path("/tmp/fake")

    def read_latest_usage(self, session_path, *, fallback_window=None):
        return self._report


class _RecordingRecycleTransport:
    def __init__(self) -> None:
        self.spawned: list[tuple[str, Path | None]] = []
        self.terminated: list[str] = []
        self.sent: list[tuple[str, Path, str, object]] = []

    def spawn(self, role, argv, *, cwd=None):  # noqa: ANN001
        self.spawned.append((role.instance_id, cwd))

    def wait_ready(self, role_name, pattern, timeout):  # noqa: ANN001
        return True

    def terminate(self, role_name):  # noqa: ANN001
        self.terminated.append(role_name)

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


class TestHealthyInstance:
    def test_below_threshold_stays_healthy(
        self, state_dir, claude_config, transport
    ):
        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_make_usage(0.30))}
        # Pretend dev has a session_id already
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")

        orch._check_context_thresholds()
        assert orch._instance_state.get("dev", "healthy") == "healthy"

    def test_warning_below_compact_only_emits_warning(
        self, state_dir, transport
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="dev",
                    backend="claude-code",
                    context_warning_threshold=0.6,
                    context_compact_threshold=0.7,
                    context_hard_cap=0.9,
                ),
            ],
        )
        orch = Orchestrator(state_dir, cfg, transport)
        orch._session_readers = {"claude-code": _FakeReader(_make_usage(0.65))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="x", status="in_progress", assigned_to="dev"),
        )

        orch._check_context_thresholds()

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "worker.context.warning" in types
        assert "worker.context.compact.requested" not in types
        assert "worker.recycling" not in types
        assert orch._instance_state.get("dev", "healthy") == "healthy"


class TestIdleRecyclingTrigger:
    def test_above_threshold_idle_enters_recycling(
        self, state_dir, claude_config, transport
    ):
        """Idle + over threshold → recycle runs inline and state returns
        to healthy. The state machine transition is observed via the
        emitted events, not the final state slot."""
        orch = Orchestrator(state_dir, claude_config, transport)
        # Force high usage
        orch._session_readers = {"claude-code": _FakeReader(_make_usage(0.75))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")

        # No in-progress tasks → dev is idle
        orch._check_context_thresholds()

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "worker.context.warning" in types
        assert "worker.recycling" in types
        assert "worker.recycled" in types
        assert orch._instance_state.get("dev") == "healthy"

    def test_above_threshold_busy_compacts_first(
        self, state_dir, claude_config, transport
    ):
        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_make_usage(0.75))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="x", status="in_progress", assigned_to="dev"),
        )

        orch._check_context_thresholds()
        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "worker.context.compact.requested" in types
        assert "worker.context.compacted" in types
        assert "worker.recycling" not in types
        assert orch._instance_state.get("dev") == "healthy"


class TestHardCap:
    def test_above_hard_cap_emits_critical_event(
        self, state_dir, claude_config, transport
    ):
        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_make_usage(0.95))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")
        # Busy so it stays in pending_recycle not recycling
        TaskStore(state_dir / "kanban.json").add(
            Task(
                id="T1",
                title="x",
                status="in_progress",
                assigned_to="dev",
                active_dispatch_id="disp-1",
            ),
        )
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="runtime.snapshot.recorded",
            actor="orchestrator",
            task_id="T1",
            payload={
                "schema_version": "runtime-snapshot.v1",
                "snapshot_id": "snap-dispatch-T1-disp-1",
                "snapshot_ref": ".zf/snapshots/T1/disp-1/runtime-snapshot.json",
                "source": "dispatch",
                "task_id": "T1",
                "dispatch_id": "disp-1",
            },
        ))

        orch._check_context_thresholds()
        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "worker.context.critical" in types
        critical = [e for e in events if e.type == "worker.context.critical"][-1]
        assert critical.task_id == "T1"
        assert critical.payload["task_id"] == "T1"
        assert critical.payload["dispatch_id"] == "disp-1"
        assert critical.payload["role"] == "dev"
        assert critical.payload["instance_id"] == "dev"
        assert critical.payload["backend"] == "claude-code"
        assert critical.payload["context_usage_ratio"] == pytest.approx(0.95)
        assert critical.payload["ratio"] == pytest.approx(0.95)
        assert critical.payload["session_ref"]
        assert critical.payload["source"] == "session_reader"
        assert critical.payload["reason"] == "hard_cap_exceeded"
        assert critical.payload["snapshot_ref"] == ".zf/snapshots/T1/disp-1/runtime-snapshot.json"
        routed = [e for e in events if e.type == "completion_audit.routed"][-1]
        assert routed.task_id == "T1"
        assert routed.causation_id == critical.id
        assert routed.payload["route"] == "retry"
        assert routed.payload["resume_packet_path"]
        assert routed.payload["previous_snapshot_ref"] == ".zf/snapshots/T1/disp-1/runtime-snapshot.json"
        assert routed.payload["recovery_snapshot_ref"]
        assert (state_dir / "resume_packets" / "T1.json").exists()
        assert "task.retry_scheduled" in types

    def test_warning_event_always_emitted_on_trip(
        self, state_dir, claude_config, transport
    ):
        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_make_usage(0.75))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")
        TaskStore(state_dir / "kanban.json").add(
            Task(id="T1", title="x", status="in_progress", assigned_to="dev"),
        )

        orch._check_context_thresholds()
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(e.type == "worker.context.warning" for e in events)


class TestMockBackendBypass:
    def test_mock_backend_skipped(self, state_dir, transport):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        orch = Orchestrator(state_dir, cfg, transport)
        orch._check_context_thresholds()
        # Should not crash and no state registered
        assert "dev" not in orch._instance_state


class TestPendingRecycleDrain:
    def test_pending_advances_to_recycling_when_idle(
        self, state_dir, claude_config, transport
    ):
        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_make_usage(0.75))}
        reg = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        reg.get_or_create("dev")
        reg.mark_spawned("dev")

        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))
        orch._context_compact_attempted = {"dev"}
        orch._check_context_thresholds()
        assert orch._instance_state["dev"] == "pending_recycle"

        # Task completes → archived
        store.update("T1", status="done")
        orch._check_pending_recycles()
        # After drain, dev should be healthy again (recycle ran + reset)
        assert orch._instance_state.get("dev") in ("healthy", "recycling")

    def test_pending_stays_when_still_busy(
        self, state_dir, claude_config, transport
    ):
        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_make_usage(0.75))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))
        orch._context_compact_attempted = {"dev"}

        orch._check_context_thresholds()
        assert orch._instance_state["dev"] == "pending_recycle"
        orch._check_pending_recycles()
        # Still busy, state unchanged
        assert orch._instance_state["dev"] == "pending_recycle"

    def test_restart_recovered_idle_liveness_clears_pending_dispatch_gate(
        self, state_dir, claude_config, transport
    ):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="worker.state.changed",
            actor="dev",
            payload={
                "from": "busy",
                "to": "pending_recycle",
                "reason": "context ratio 0.75, busy",
            },
        ))
        reg = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        reg.get_or_create("dev")
        reg.record_heartbeat("dev", {
            "instance_id": "dev",
            "state": "active",
            "current_task_id": "",
            "source": "agent.usage",
        })

        orch = Orchestrator(state_dir, claude_config, transport)

        assert orch._last_worker_state["dev"] == "pending_recycle"
        assert orch._worker_dispatchable("dev") is True
        assert orch._last_worker_state["dev"] == "idle"
        events = log.read_all()
        recovered = [
            event for event in events
            if event.type == "worker.state.changed"
            and event.actor == "dev"
            and event.payload.get("to") == "idle"
        ]
        assert recovered
        fresh_reg = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        _, payload = fresh_reg.get_last_heartbeat("dev")
        assert payload is not None
        assert payload["state"] == "idle"


class TestFanoutChildRecycle:
    def test_active_fanout_child_blocks_idle_recycle(
        self, state_dir, claude_config, transport
    ):
        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_make_usage(0.75))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="fanout.child.dispatched",
            actor="zf-cli",
            payload={
                "fanout_id": "fanout-scan-1",
                "trace_id": "trace-scan",
                "stage_id": "scan",
                "child_id": "scan-runtime",
                "run_id": "run-fanout-scan-1-scan-runtime",
                "role_instance": "dev",
            },
            correlation_id="trace-scan",
        ))

        orch._check_context_thresholds()
        orch._check_pending_recycles()

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [event.type for event in events]
        assert "worker.context.warning" in types
        assert "worker.recycling" not in types
        assert orch._instance_state["dev"] == "pending_recycle"
        warning = [event for event in events
                   if event.type == "worker.context.warning"][-1]
        assert warning.payload["idle"] is False
        assert warning.payload["active_fanout"]["child_id"] == "scan-runtime"

    def test_recycle_reinjects_active_fanout_briefing(
        self, state_dir, claude_config
    ):
        transport = _RecordingRecycleTransport()
        orch = Orchestrator(state_dir, claude_config, transport)  # type: ignore[arg-type]
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")
        briefing_dir = state_dir / "briefings"
        briefing_dir.mkdir()
        briefing_path = briefing_dir / "dev-fanout-scan-1-scan-runtime.md"
        briefing_path.write_text("fanout scan briefing\n", encoding="utf-8")
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="fanout.child.dispatched",
            actor="zf-cli",
            payload={
                "fanout_id": "fanout-scan-1",
                "trace_id": "trace-scan",
                "stage_id": "scan",
                "child_id": "scan-runtime",
                "run_id": "run-fanout-scan-1-scan-runtime",
                "role_instance": "dev",
                "briefing_path": str(briefing_path),
                "snapshot_ref": ".zf/snapshots/fanout-scan-1/run-fanout-scan-1-scan-runtime/runtime-snapshot.json",
            },
            correlation_id="trace-scan",
        ))

        orch._start_recycle(claude_config.roles[0])

        assert transport.terminated == ["dev"]
        assert transport.sent
        assert transport.sent[-1][0] == "dev"
        assert transport.sent[-1][1] == briefing_path
        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [event.type for event in events]
        assert "worker.recovery.injected" in types
        injected = [event for event in events if event.type == "worker.recovery.injected"][-1]
        assert injected.payload["snapshot_ref"] == ".zf/snapshots/fanout-scan-1/run-fanout-scan-1-scan-runtime/runtime-snapshot.json"
        assert not any(
            event.type == "worker.recovery.skipped"
            and event.payload.get("reason") == "idle_after_recycle"
            for event in events
        )
        assert orch._last_worker_state["dev"] == "busy"


class TestDoubleTriggerNoOp:
    def test_already_recycling_ignored(
        self, state_dir, claude_config, transport
    ):
        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_make_usage(0.75))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")

        # Force state already in recycling
        orch._instance_state["dev"] = "recycling"
        warnings_before = [
            e for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "worker.context.warning"
        ]
        orch._check_context_thresholds()
        warnings_after = [
            e for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "worker.context.warning"
        ]
        assert len(warnings_after) == len(warnings_before)  # no new warning


class TestRunOnceContextPreflight:
    def test_run_once_recycles_idle_high_context_before_dispatch(
        self, state_dir, claude_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="backlog", assigned_to="dev"))
        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_make_usage(0.75))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")

        decisions = orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert any(d.action == "dispatch" and d.task_id == "T1" for d in decisions)
        assert types.index("worker.recycled") < types.index("task.dispatched")
        assert orch._instance_state.get("dev") == "healthy"
        assert orch._last_worker_state.get("dev") == "busy"
