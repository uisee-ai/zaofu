from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.core.config.schema import (
    ProjectConfig,
    RuntimeConfig,
    RuntimeRunManagerConfig,
    RuntimeRunManagerResidentAgentConfig,
    RuntimeRunManagerSourceRepairConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.run_manager_watchdog import (
    RUN_MANAGER_RESIDENT_RESTART_REQUESTED,
    RUN_MANAGER_SOURCE_REPAIR_DISPATCH_REQUESTED,
    RUN_MANAGER_UNHEALTHY,
    run_manager_watchdog_tick,
)


def _state(tmp_path: Path) -> tuple[Path, EventLog, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "projections").mkdir()
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log)


def _config(*, source_repair: bool = False, resident: bool = False) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="watchdog-test"),
        session=SessionConfig(tmux_session="zf-watchdog"),
        runtime=RuntimeConfig(
            run_manager=RuntimeRunManagerConfig(
                backend="codex",
                resident_agent=RuntimeRunManagerResidentAgentConfig(
                    enabled=resident,
                    session_mode="dedicated",
                    tmux_session="zf-watchdog-run-manager",
                    instance_id="run-manager",
                ),
                source_repair=RuntimeRunManagerSourceRepairConfig(
                    enabled=source_repair,
                    backend="codex",
                ),
            ),
        ),
    )


def test_watchdog_emits_unhealthy_for_tick_started_timeout(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)
    started_at = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
    log.append(ZfEvent(
        type="run.manager.tick.started",
        id="evt-started",
        ts=started_at.isoformat(),
        payload={},
    ))

    result = run_manager_watchdog_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        now=started_at + timedelta(seconds=301),
        max_tick_seconds=300,
    )

    events = log.read_all()
    assert result.unhealthy_emitted == 1
    unhealthy = [event for event in events if event.type == RUN_MANAGER_UNHEALTHY][-1]
    assert unhealthy.payload["reason"] == "tick_started_timeout"


def test_watchdog_requests_resident_restart_for_dead_pane(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)

    result = run_manager_watchdog_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(resident=True),
        event_log=log,
        resident_probe=lambda: {
            "ok": False,
            "target": "zf-watchdog-run-manager:run-manager",
            "reason": "pane_missing",
        },
    )

    events = log.read_all()
    assert result.resident_restart_requested == 1
    request = [
        event for event in events
        if event.type == RUN_MANAGER_RESIDENT_RESTART_REQUESTED
    ][-1]
    assert request.payload["restart_scope"] == "resident_only"
    assert request.payload["tmux_session"] == "zf-watchdog-run-manager"
    assert request.payload["first_tick_mode"] == "observe_only"


def test_watchdog_source_repair_dispatch_requires_enablement(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)
    for i in range(3):
        log.append(ZfEvent(
            type="run.manager.tick.failed",
            id=f"evt-failed-{i}",
            payload={"reason": "boom"},
        ))

    result = run_manager_watchdog_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(source_repair=True),
        event_log=log,
        tick_failure_threshold=3,
    )

    events = log.read_all()
    assert result.source_repair_requested == 1
    request = [
        event for event in events
        if event.type == RUN_MANAGER_SOURCE_REPAIR_DISPATCH_REQUESTED
    ][-1]
    assert request.payload["source_repair"]["enabled"] is True
    assert request.payload["source_repair"]["restart_policy"] == "never_during_active_run"
    assert request.payload["repair_task_payload"]["contract"]["scope"] == [
        "src/zf/**",
        "tests/**",
        "docs/**",
    ]


def test_watchdog_ignores_stale_projection_after_quiescent_ship(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)
    terminal_at = datetime(2026, 7, 15, 7, 0, tzinfo=timezone.utc)
    log.append(ZfEvent(
        type="run.goal.started",
        correlation_id="run-terminal",
        payload={"workflow_run_id": "run-terminal"},
    ))
    log.append(ZfEvent(
        type="ship.completed",
        ts=terminal_at.isoformat(),
        correlation_id="run-terminal",
        payload={"workflow_run_id": "run-terminal"},
    ))
    projection = state_dir / "projections" / "run_manager.json"
    projection.write_text("{}\n", encoding="utf-8")
    stale_at = (terminal_at - timedelta(hours=1)).timestamp()
    os.utime(projection, (stale_at, stale_at))

    result = run_manager_watchdog_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(source_repair=True, resident=True),
        event_log=log,
        now=terminal_at + timedelta(hours=1),
        resident_probe=lambda: {"ok": False, "reason": "pane_missing"},
    )

    assert result.changed is False
    assert not [event for event in log.read_all() if event.type == RUN_MANAGER_UNHEALTHY]
