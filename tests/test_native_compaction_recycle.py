"""A bounded native compact must get time to finish before forced recycling."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.backend_session_reader import UsageReport
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def worker(tmp_path):
    state = tmp_path / ".runtime"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n")
    SessionStore(state / "session.yaml").create(project_root=str(tmp_path))
    RoleSessionRegistry(state / "role_sessions.yaml", project_root=str(tmp_path)).get_or_create("dev")
    TaskStore(state / "kanban.json").add(Task(id="T1", title="work", status="in_progress", assigned_to="dev"))
    role = RoleConfig(name="dev", backend="codex", drain_hold_seconds=180)
    transport = TmuxTransport(TmuxSession("test", dry_run=True))
    runtime = Orchestrator(state, ZfConfig(project=ProjectConfig(name="test"), roles=[role]), transport)
    usage = UsageReport(190000, 100, 200000, .95, "2026-11-08T07:43:14Z")
    runtime._session_readers = {"codex": SimpleNamespace(
        session_path=lambda *a, **k: Path("/unused"),
        read_latest_usage=lambda *a, **k: usage,
    )}
    recycled = []
    runtime._start_recycle = lambda role: recycled.append(role.instance_id)
    runtime._now = lambda: 0
    runtime._check_context_thresholds()
    assert runtime._instance_state["dev"] == "pending_recycle"
    return runtime, transport, usage, recycled


def test_native_compact_defers_once_then_hard_recycle_remains_bounded(worker):
    runtime, transport, _, recycled = worker
    transport.capture_log = lambda *a, **k: "◦ Compacting context (1m 12s • esc to interrupt)\n› Ask Codex to do anything"
    for now in (200, 250, 379):
        runtime._now = lambda: now
        runtime._check_context_thresholds()
        assert recycled == []
    warnings = [e for e in runtime.event_log.read_all() if e.payload.get("reason") == "native_compaction_recycle_grace"]
    assert len(warnings) == 1
    assert warnings[0].payload["defer_until_epoch"] == 380
    runtime._now = lambda: 381
    runtime._check_context_thresholds()
    assert recycled == ["dev"]


def test_completed_compact_waits_for_fresh_usage_then_normal_drain(worker):
    runtime, transport, usage, recycled = worker
    transport.capture_log = lambda *a, **k: "◦ Compacting context (40s • esc to interrupt)"
    runtime._now = lambda: 200
    runtime._check_context_thresholds()
    transport.capture_log = lambda *a, **k: "• Context compacted · 1m 30s\n◦ Working (1h • esc to interrupt)"
    runtime._now = lambda: 220
    runtime._check_context_thresholds()
    assert recycled == []  # Last usage still describes the pre-compaction input.
    usage.ratio = .2
    usage.timestamp = "2026-11-08T07:45:00Z"
    runtime._check_context_thresholds()
    assert "dev" not in runtime._hard_cap_exceeded
    assert recycled == []


@pytest.mark.parametrize("pane", [
    "◦ Compacting context (40s • esc to interrupt)\n◦ Working (1h • esc to interrupt)",
    "› Please print Compacting context (40s • esc to interrupt)",
    "• Context compacted · 1m 30s",
    "",
])
def test_history_or_ordinary_activity_does_not_delay_forced_recycle(worker, pane):
    runtime, transport, _, recycled = worker
    transport.capture_log = lambda *a, **k: pane
    runtime._now = lambda: 200
    runtime._check_context_thresholds()
    assert recycled == ["dev"]


def test_unavailable_pane_does_not_disable_hard_cap(worker):
    runtime, transport, _, recycled = worker
    def unavailable(*args, **kwargs):
        raise RuntimeError("pane absent")
    transport.capture_log = unavailable
    runtime._now = lambda: 200
    runtime._check_context_thresholds()
    assert recycled == ["dev"]


def test_compaction_grace_does_not_transfer_to_another_provider_session(worker):
    runtime, transport, _, recycled = worker
    transport.capture_log = lambda *a, **k: "◦ Compacting context (40s • esc to interrupt)"
    runtime._now = lambda: 200
    runtime._check_context_thresholds()
    assert recycled == []
    RoleSessionRegistry(
        runtime.state_dir / "role_sessions.yaml", project_root=str(runtime.project_root),
    ).rotate("dev")
    transport.capture_log = lambda *a, **k: "◦ Working (1m • esc to interrupt)"
    runtime._now = lambda: 220
    runtime._check_context_thresholds()
    assert recycled == ["dev"]
