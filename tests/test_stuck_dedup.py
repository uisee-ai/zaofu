"""ω-1.c: heartbeat sweep stuck event dedup (Class F debt).

Per docs/design/38-omega-1-baseline-and-verdict.md §5 + backlog
backlogs/2026-05-18-0243-omega-1c-stuck-event-dedup.md.

r-next-10 末段 events.jsonl 含:
    02:31:45 worker.stuck × 5
    02:32:49 worker.stuck × 5
    02:33:52 worker.stuck × 5

α-3 sweep 每 60s tick 重发同 instance 的 stuck/silent → events.jsonl
spam。本 fix 加 per-(instance, signal_type) 300s cooldown。
heartbeat 来后清 dedup，下次 stuck 仍立刻可发。
"""

from __future__ import annotations

import time as _time
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
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


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
                publishes=["dev.build.done"],
            ),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True))


@pytest.fixture
def orch(state_dir, config, transport):
    return Orchestrator(state_dir, config, transport)


def _plant_stuck_heartbeat(
    state_dir: Path, instance_id: str, age_seconds: float,
) -> None:
    reg = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(state_dir.parent),
    )
    reg.get_or_create(instance_id)
    # Since 83b451c a busy heartbeat whose current_task_id is missing
    # from kanban is stale evidence and the sweep skips emission
    # (_heartbeat_current_task_still_owned -> False), so the stuck
    # premise needs a real in_progress task assigned to this instance.
    task_id = f"TASK-{instance_id}"
    store = TaskStore(state_dir / "kanban.json")
    if all(t.id != task_id for t in store.list_all()):
        store.add(Task(
            id=task_id, title="wip", status="in_progress",
            assigned_to=instance_id,
        ))
    reg.record_heartbeat(instance_id, {
        "instance_id": instance_id,
        "state": "busy",
        "current_task_id": task_id,
        "last_action_ts": "now",
    })
    fake_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ).isoformat()
    reg._meta[instance_id]["last_heartbeat_at"] = fake_ts
    reg._save()


def _count_events(state_dir: Path, event_type: str) -> int:
    log = EventLog(state_dir / "events.jsonl")
    return sum(1 for e in log.read_all() if e.type == event_type)


# ─── ω-1.c dedup behavior ────────────────────────────────────────────────


def test_first_stuck_emits_once(state_dir, orch):
    _plant_stuck_heartbeat(state_dir, "dev-1", age_seconds=240)

    orch._run_heartbeat_sweep()

    assert _count_events(state_dir, "worker.stuck") == 1


def test_second_sweep_within_cooldown_skips_emit(state_dir, orch):
    """Second sweep within 300s cooldown for the same instance must NOT
    emit a second worker.stuck event."""
    _plant_stuck_heartbeat(state_dir, "dev-1", age_seconds=240)

    orch._run_heartbeat_sweep()
    orch._run_heartbeat_sweep()  # immediate second call

    assert _count_events(state_dir, "worker.stuck") == 1


def test_three_consecutive_sweeps_only_one_stuck(state_dir, orch):
    """Replays r-next-10 pattern (02:31:45 / 02:32:49 / 02:33:52) — three
    sweeps within cooldown should produce ONE worker.stuck, not three."""
    _plant_stuck_heartbeat(state_dir, "dev-1", age_seconds=240)

    for _ in range(3):
        orch._run_heartbeat_sweep()

    assert _count_events(state_dir, "worker.stuck") == 1


def test_different_instances_each_emit_once(state_dir, orch):
    """Dedup is per-instance: 5 different stuck workers each emit one
    worker.stuck on the first sweep."""
    for i in range(1, 6):
        _plant_stuck_heartbeat(state_dir, f"dev-{i}", age_seconds=240)

    orch._run_heartbeat_sweep()

    assert _count_events(state_dir, "worker.stuck") == 5


def test_heartbeat_after_stuck_clears_dedup(state_dir, orch):
    """When the worker recovers (emits a fresh heartbeat), subsequent
    stuck conditions are allowed to emit again — dedup state is cleared
    by housekeeping on heartbeat receipt."""
    _plant_stuck_heartbeat(state_dir, "dev-1", age_seconds=240)
    orch._run_heartbeat_sweep()
    assert _count_events(state_dir, "worker.stuck") == 1

    # Simulate worker recovery via worker.heartbeat housekeeping
    recovery_event = ZfEvent(
        type="worker.heartbeat",
        actor="dev-1",
        payload={
            "instance_id": "dev-1",
            "state": "idle",
            "last_action_ts": datetime.now(timezone.utc).isoformat(),
        },
    )
    orch._apply_housekeeping(recovery_event)

    # Now worker gets stuck again (age was reset by housekeeping; replant)
    _plant_stuck_heartbeat(state_dir, "dev-1", age_seconds=240)
    orch._run_heartbeat_sweep()

    assert _count_events(state_dir, "worker.stuck") == 2


def test_dedup_also_applies_to_probe_silent(state_dir, orch):
    """worker.probe.silent should also be deduped per same scheme to
    keep r-next-10-style spam down."""
    # Age between silent threshold (90s) and stuck threshold (180s)
    _plant_stuck_heartbeat(state_dir, "dev-1", age_seconds=120)

    orch._run_heartbeat_sweep()
    orch._run_heartbeat_sweep()
    orch._run_heartbeat_sweep()

    assert _count_events(state_dir, "worker.probe.silent") == 1


def test_dedup_field_in_audited_fields():
    """ω-1.c adds _sweep_signal_last_emit_at to Orchestrator.__init__;
    state-recoverability auditor must accept it (as transient)."""
    from tests.test_state_recoverability import _AUDITED_FIELDS

    assert "_sweep_signal_last_emit_at" in _AUDITED_FIELDS


# ─── wire-up grep ────────────────────────────────────────────────────────


def test_wire_up_dedup_state_exists_on_orchestrator():
    src = Path(__file__).resolve().parents[1] / "src/zf/runtime/orchestrator.py"
    text = src.read_text(encoding="utf-8")
    assert "_sweep_signal_last_emit_at" in text, (
        "ω-1.c wire-up missing: Orchestrator has no _sweep_signal_last_emit_at"
    )
