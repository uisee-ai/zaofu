from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.dispatch_diagnostics import (
    build_dispatch_diagnostics,
    build_worker_availability,
)


def _state_dir(tmp_path: Path) -> Path:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def test_dispatch_diagnostics_reports_ready_task_without_worker(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="ready but not dispatched",
        status="backlog",
        contract=TaskContract(owner_role="dev"),
    ))

    diagnostics = build_dispatch_diagnostics(state_dir)

    kinds = {item["kind"] for item in diagnostics["notifications"]}
    assert diagnostics["ready_task_count"] == 1
    assert "ready_task_no_dispatchable_worker" in kinds
    assert "loop_unavailable_for_ready_tasks" in kinds


def test_dispatch_diagnostics_counts_idle_worker_as_dispatchable(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    registry = RoleSessionRegistry(state_dir / "role_sessions.yaml", str(tmp_path))
    registry.record_heartbeat("dev-1", {
        "instance_id": "dev-1",
        "state": "idle",
        "current_task_id": "",
    })
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        roles=[RoleConfig(name="dev", instance_id="dev-1")],
    )

    diagnostics = build_dispatch_diagnostics(
        state_dir,
        config=config,
        project_root=tmp_path,
    )

    assert diagnostics["dispatchable_worker_count"] == 1
    worker = diagnostics["worker_availability"][0]
    assert worker["instance_id"] == "dev-1"
    assert worker["availability"] == "dispatchable"


def test_dispatch_diagnostics_surfaces_assigned_without_dispatch(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    now = datetime.now(timezone.utc)
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id="TASK-1",
        ts=(now - timedelta(seconds=60)).isoformat(),
        payload={"assignee": "dev-1"},
    ))

    diagnostics = build_dispatch_diagnostics(state_dir)

    assert any(
        item["kind"] == "assigned_without_dispatch"
        and item["task_id"] == "TASK-1"
        for item in diagnostics["notifications"]
    )


def test_fanout_child_dispatch_satisfies_assigned_without_dispatch(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    now = datetime.now(timezone.utc)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id="TASK-1",
        ts=(now - timedelta(seconds=60)).isoformat(),
        payload={"assignee": "dev-1"},
    ))
    log.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        ts=(now - timedelta(seconds=5)).isoformat(),
        payload={"task_id": "TASK-1", "role_instance": "dev-1"},
    ))

    diagnostics = build_dispatch_diagnostics(state_dir)

    assert not any(
        item["kind"] == "assigned_without_dispatch"
        and item["task_id"] == "TASK-1"
        for item in diagnostics["notifications"]
    )


def test_fanout_child_dispatch_clears_repeated_dispatch_skipped(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="orchestrator.dispatch_skipped",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"reason": "no_available_role"},
    ))
    log.append(ZfEvent(
        type="orchestrator.dispatch_skipped",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"reason": "no_available_role"},
    ))
    log.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={"task_id": "TASK-1", "role_instance": "dev-1"},
    ))

    diagnostics = build_dispatch_diagnostics(state_dir)

    assert not any(
        item["kind"] == "repeated_dispatch_skipped"
        and item["task_id"] == "TASK-1"
        for item in diagnostics["notifications"]
    )


def test_worker_availability_filters_terminal_active_task_from_heartbeat(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-1", title="done", status="in_progress", assigned_to="dev-1"))
    store.update("TASK-1", status="done")
    registry = RoleSessionRegistry(state_dir / "role_sessions.yaml", str(tmp_path))
    registry.record_heartbeat("dev-1", {
        "instance_id": "dev-1",
        "state": "awaiting_review",
        "current_task_id": "TASK-1",
    })
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="dev-1",
        task_id="TASK-1",
        payload={"to": "awaiting_review", "task_id": "TASK-1"},
    ))
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        roles=[RoleConfig(name="dev", instance_id="dev-1")],
    )

    workers = build_worker_availability(
        state_dir,
        config=config,
        project_root=tmp_path,
        events=log.read_all(),
    )

    worker = workers[0]
    assert worker["active_task"] == ""
    assert worker["state"] == "idle"
    assert worker["availability"] == "dispatchable"


def test_worker_availability_clears_missing_stale_task_from_heartbeat(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    registry = RoleSessionRegistry(state_dir / "role_sessions.yaml", str(tmp_path))
    registry.record_heartbeat("dev-1", {
        "instance_id": "dev-1",
        "state": "awaiting_review",
        "current_task_id": "TASK-REMOVED",
    })
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        roles=[RoleConfig(name="dev", instance_id="dev-1")],
    )

    workers = build_worker_availability(
        state_dir,
        config=config,
        project_root=tmp_path,
        events=[],
    )

    worker = workers[0]
    assert worker["stale_task_id"] == "TASK-REMOVED"
    assert worker["active_task"] == ""
    assert worker["state"] == "idle"
    assert worker["availability"] == "dispatchable"


def test_worker_availability_idle_event_clears_stale_event_task(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="requeued after stop",
        status="backlog",
        contract=TaskContract(owner_role="dev"),
    ))
    registry = RoleSessionRegistry(state_dir / "role_sessions.yaml", str(tmp_path))
    registry.record_heartbeat("dev-1", {
        "instance_id": "dev-1",
        "state": "idle",
        "current_task_id": "",
    })
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.heartbeat",
        actor="dev-1",
        task_id="TASK-1",
        payload={
            "instance_id": "dev-1",
            "state": "busy",
            "current_task_id": "TASK-1",
        },
    ))
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="dev-1",
        payload={
            "instance_id": "dev-1",
            "from": "busy",
            "to": "idle",
            "reason": "restart recovery completed",
        },
    ))
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        roles=[RoleConfig(name="dev", instance_id="dev-1")],
    )

    diagnostics = build_dispatch_diagnostics(
        state_dir,
        config=config,
        project_root=tmp_path,
    )

    worker = diagnostics["worker_availability"][0]
    assert worker["active_task"] == ""
    assert worker["availability"] == "dispatchable"
    assert diagnostics["dispatchable_worker_count"] == 1
    assert not any(
        item["kind"] == "ready_task_no_dispatchable_worker"
        and item["task_id"] == "TASK-1"
        for item in diagnostics["notifications"]
    )
