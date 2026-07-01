"""α-3+: proactive dispatch — idle worker + ready backlog → wake.

When the heartbeat sweep identifies an idle instance AND the task
store has at least one task ready for dispatch, emit
``worker.probe.idle`` so the watcher wakes and the existing dispatch
path picks the task up.

Per docs/design/36 §4.3 + the leftover acceptance from the α-2/3
backlog (proactive dispatch was identified as α-3 follow-up, lands
in this turn alongside the watcher-tick wire-up).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


# ─── event registration ──────────────────────────────────────────────────


def test_worker_probe_idle_in_known_types():
    from zf.core.events.known_types import KNOWN_EVENT_TYPES

    assert "worker.probe.idle" in KNOWN_EVENT_TYPES


def test_worker_probe_idle_in_wake_patterns():
    from zf.runtime.wake_patterns import WAKE_PATTERNS

    assert "worker.probe.idle" in WAKE_PATTERNS


# ─── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    (sd / "logs").mkdir()
    log = EventLog(sd / "events.jsonl")
    log.append(ZfEvent(type="session.started", actor="zf-cli"))
    log.append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def config():
    return ZfConfig(
        project=ProjectConfig(name="test"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[
            RoleConfig(
                name="dev",
                backend="mock",
                stages=["implement"],
                publishes=["dev.build.done", "dev.blocked"],
            ),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True))


def _plant_idle_heartbeat(state_dir: Path, instance_id: str) -> None:
    reg = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(state_dir.parent),
    )
    reg.get_or_create(instance_id)
    reg.record_heartbeat(instance_id, {
        "instance_id": instance_id,
        "state": "idle",
        "current_task_id": "",
        "last_action_ts": datetime.now(timezone.utc).isoformat(),
    })


def _plant_ready_task(state_dir: Path, *, role: str = "dev") -> str:
    store = TaskStore(state_dir / "kanban.json")
    task = Task(
        id="TASK-READY",
        title="ready task for proactive dispatch",
        status="backlog",
        contract=TaskContract(behavior="implement X"),
    )
    store.add(task)
    return task.id


# ─── proactive emit on (idle worker + ready backlog) ─────────────────────


def test_sweep_emits_worker_probe_idle_when_backlog_ready(
    state_dir, config, transport,
):
    """Idle worker observed AND backlog has a ready task → sweep emits
    worker.probe.idle so the next run_once dispatch tries to assign it.
    """
    _plant_idle_heartbeat(state_dir, "dev-1")
    _plant_ready_task(state_dir)

    orch = Orchestrator(state_dir, config, transport)
    orch._run_heartbeat_sweep()

    log = EventLog(state_dir / "events.jsonl")
    events = list(log.read_all())
    probe_idle = [e for e in events if e.type == "worker.probe.idle"]

    assert len(probe_idle) >= 1
    p = probe_idle[-1].payload or {}
    assert p.get("instance_id") == "dev-1"
    assert p.get("ready_backlog_count", 0) >= 1


def test_sweep_no_probe_idle_when_backlog_empty(state_dir, config, transport):
    """Idle worker but no backlog → no wake spam. Web UI badges can show
    idle from last_heartbeat_at directly; this event is only for the
    'go dispatch now' signal."""
    _plant_idle_heartbeat(state_dir, "dev-1")
    # No backlog task planted

    orch = Orchestrator(state_dir, config, transport)
    orch._run_heartbeat_sweep()

    log = EventLog(state_dir / "events.jsonl")
    events = list(log.read_all())
    probe_idle = [e for e in events if e.type == "worker.probe.idle"]

    assert len(probe_idle) == 0


def test_sweep_no_probe_idle_when_no_idle_workers(state_dir, config, transport):
    """No idle workers (registry empty) → no probe.idle even if backlog
    has work."""
    _plant_ready_task(state_dir)
    # No heartbeats planted

    orch = Orchestrator(state_dir, config, transport)
    orch._run_heartbeat_sweep()

    log = EventLog(state_dir / "events.jsonl")
    events = list(log.read_all())
    probe_idle = [e for e in events if e.type == "worker.probe.idle"]

    assert len(probe_idle) == 0


def test_sweep_emits_one_probe_idle_per_idle_worker(state_dir, config, transport):
    """Multiple idles + multiple ready tasks → one probe.idle per idle
    worker, with ready_backlog_count carrying total backlog size."""
    _plant_idle_heartbeat(state_dir, "dev-1")
    _plant_idle_heartbeat(state_dir, "dev-2")
    _plant_ready_task(state_dir)
    # Plant a 2nd ready task with a different id
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-READY-2",
        title="another ready task",
        status="backlog",
        contract=TaskContract(behavior="implement Y"),
    ))

    orch = Orchestrator(state_dir, config, transport)
    orch._run_heartbeat_sweep()

    log = EventLog(state_dir / "events.jsonl")
    probe_idle = [e for e in log.read_all() if e.type == "worker.probe.idle"]
    instance_ids = sorted([
        (e.payload or {}).get("instance_id") for e in probe_idle
    ])

    assert instance_ids == ["dev-1", "dev-2"]
    # Each event carries the backlog count
    for ev in probe_idle:
        assert (ev.payload or {}).get("ready_backlog_count") >= 2


# ─── wire-up: start.py uses _run_heartbeat_sweep on tick ────────────────


def test_wire_up_start_py_calls_heartbeat_sweep_on_tick():
    root = Path(__file__).resolve().parents[1]
    start_text = (root / "src/zf/cli/start.py").read_text(encoding="utf-8")
    services_text = (
        root / "src/zf/runtime/tick_services.py"
    ).read_text(encoding="utf-8")
    assert "run_standard_tick_services" in start_text, (
        "wire-up missing: start.py does not call shared tick services"
    )
    assert "_run_heartbeat_sweep" in services_text, (
        "wire-up missing: tick services do not call orchestrator._run_heartbeat_sweep"
    )
    assert "_run_zaofu_bug_scan" in services_text, (
        "wire-up missing: tick services do not call orchestrator._run_zaofu_bug_scan"
    )
    assert "run_supervisor_inspection" in services_text, (
        "wire-up missing: tick services do not refresh Supervisor Inspection projections"
    )
