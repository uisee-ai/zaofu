"""Completed on-demand work must not leave an unused replacement provider."""

import json
from dataclasses import replace

import pytest

from tests.test_role_lifecycle_runtime import _runtime
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.events.model import ZfEvent
from zf.core.state.git_state import GitState
from zf.runtime.idle_context_recycle import hibernate_idle_context_recycle


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
def test_completed_on_demand_recycle_sleeps_until_next_dispatch(tmp_path, backend):
    runtime, role, transport = _runtime(tmp_path)
    role.backend = backend
    runtime._ensure_role_active(role, task_id="TASK-OLD")
    registry = RoleSessionRegistry(
        runtime.state_dir / "role_sessions.yaml", project_root=tmp_path,
    )
    if backend == "codex":
        rollout = tmp_path / "rollout.jsonl"
        rollout.write_text(json.dumps({
            "type": "session_meta", "payload": {"cwd": str(tmp_path)},
        }) + "\n")
        registry.bind_codex_session(
            role.instance_id, "58585858-5858-5858-5858-585858585858",
            session_path=rollout,
        )
    # Recycling updates worker activity just before entering this path. It
    # must still release the process without waiting for ordinary idle age.
    role.lifecycle = replace(role.lifecycle, idle_seconds=120)
    runtime._set_worker_state(role.instance_id, "recycling", force=True)
    launches = len(transport.spawned)

    runtime._start_recycle(role)

    events = runtime.event_log.read_all()
    assert not [e for e in events if e.type == "worker.recycle.failed"]
    assert len(transport.spawned) == launches
    assert role.instance_id not in transport.alive
    assert runtime._last_worker_state[role.instance_id] == "suspended"
    assert runtime._instance_state[role.instance_id] == "healthy"
    assert any(e.type == "worker.recycled" for e in events)
    reloaded = RoleSessionRegistry(
        runtime.state_dir / "role_sessions.yaml", project_root=tmp_path,
    )
    assert reloaded.instance_meta()[role.instance_id]["lifecycle_state"] == "suspended"
    if backend == "codex":
        assert reloaded.get(role.instance_id) is None
        assert rollout.exists()

    # The watcher consumes these emitted lifecycle events on its next tick.
    # Replaying them must not strand the sleeping role in "recycling".
    for event in events:
        if event.type == "worker.state.changed":
            runtime._on_worker_state_changed_event(event)
    assert runtime._instance_state[role.instance_id] == "healthy"
    assert runtime._last_worker_state[role.instance_id] == "suspended"

    runtime._ensure_role_active(role, task_id="TASK-NEW")
    assert len(transport.spawned) == launches + 1
    assert role.instance_id in transport.alive
    assert "resume" not in transport.spawned[-1][1]


@pytest.mark.parametrize("blocker", ["operation", "dirty_workdir", "identity"])
def test_idle_recycle_keeps_existing_suspension_safety_gates(tmp_path, monkeypatch, blocker):
    runtime, role, transport = _runtime(tmp_path)
    runtime._ensure_role_active(role, task_id="TASK-OLD")
    registry = RoleSessionRegistry(
        runtime.state_dir / "role_sessions.yaml", project_root=tmp_path,
    )
    old_session = registry.get(role.instance_id)
    if blocker == "operation":
        runtime.event_writer.append(ZfEvent(
            type="workflow.operation.requested", actor="kernel", payload={
                "operation_id": "op-still-active", "operation_type": "agent",
                "request_hash": "a" * 64, "role_instance": role.instance_id,
            },
        ))
    elif blocker == "dirty_workdir":
        (runtime.state_dir / "workdirs" / role.instance_id / "project").mkdir(parents=True)
        monkeypatch.setattr(
            "zf.runtime.role_lifecycle_runtime.capture_git_state",
            lambda _path: GitState(dirty_files=["src/uncommitted.py"]),
        )
    else:
        registry.clear(role.instance_id)
        old_session = None

    assert hibernate_idle_context_recycle(runtime, role) is False
    assert role.instance_id in transport.alive
    assert transport.terminated == []
    assert RoleSessionRegistry(
        runtime.state_dir / "role_sessions.yaml", project_root=tmp_path,
    ).get(role.instance_id) == old_session


def test_idle_recycle_termination_failure_keeps_resume_identity(tmp_path, monkeypatch):
    runtime, role, transport = _runtime(tmp_path)
    runtime._ensure_role_active(role, task_id="TASK-OLD")
    registry = RoleSessionRegistry(
        runtime.state_dir / "role_sessions.yaml", project_root=tmp_path,
    )
    old_session = registry.get(role.instance_id)
    launches = len(transport.spawned)

    def fail_terminate(_instance_id):
        raise RuntimeError("provider termination failed")

    monkeypatch.setattr(transport, "terminate", fail_terminate)
    runtime._start_recycle(role)

    assert len(transport.spawned) == launches
    assert RoleSessionRegistry(
        runtime.state_dir / "role_sessions.yaml", project_root=tmp_path,
    ).get(role.instance_id) == old_session
    events = runtime.event_log.read_all()
    assert any(e.type == "worker.recycle.failed" for e in events)
    assert not any(e.type == "worker.recycled" for e in events)
