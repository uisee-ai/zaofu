"""A restarted watcher recovers a dead, still-owned interrupted recycle."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest

from tests.test_capture_logs_watchdog import _AliveControllableTransport, state_dir
from tests.test_task_workspace_recovery import workspace
from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events import EventLog, ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.session_mutex import SessionLock
from zf.runtime.workflow_operation import reduce_workflow_operations


class RecordingTransport(_AliveControllableTransport):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.argv = []

    def spawn(self, role, argv, *, cwd=None):
        super().spawn(role, argv, cwd=cwd)
        self.argv.append((role.instance_id, argv, cwd))

    def send_task(self, role_name, briefing_path, prompt):
        self.sent.append((role_name, Path(briefing_path), prompt))


def interrupted_runtime(state_dir, *, obligation="task"):
    config = ZfConfig(
        project=ProjectConfig(name="t"), session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="codex", stuck_threshold_seconds=300)],
    )
    transport = RecordingTransport()
    log = EventLog(state_dir / "events.jsonl")
    if obligation == "fanout":
        TaskStore(state_dir / "kanban.json").update("TASK-LIVENESS-DEV", status="done")
        briefing = state_dir / "briefings/reader.md"
        briefing.parent.mkdir(exist_ok=True)
        briefing.write_text("Read the original fanout inputs.\n")
        log.append(ZfEvent(type="fanout.child.dispatched", actor="orchestrator", payload={
            "fanout_id": "fanout-read", "child_id": "scan", "stage_id": "scan",
            "run_id": "reader-run", "role_instance": "dev", "briefing_path": str(briefing),
        }))
    else:
        log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                          task_id="TASK-LIVENESS-DEV", payload={
                              "role": "dev", "dispatch_id": "disp-dev",
                              "operation_id": "op-original", "attempt_id": "attempt-original",
                          }))
    identity = {
        "operation_id": "op-original", "workflow_run_id": "workflow-original",
        "task_id": "TASK-LIVENESS-DEV", "role_instance": "dev",
        "operation_type": "fanout" if obligation == "fanout" else "task-stage",
        "active_attempt_id": "attempt-original", "dispatch_id": "disp-dev",
        "lease_id": "lease-original", "operation_generation": 2,
    }
    log.append(ZfEvent(type="workflow.operation.requested", actor="orchestrator", payload=identity))
    log.append(ZfEvent(type="workflow.operation.started", actor="orchestrator", payload=identity))
    # Real worker-origin evidence is still fresh: normal liveness must not
    # independently rescue this test. A kernel state mirror is not sufficient.
    log.append(ZfEvent(type="worker.heartbeat", actor="dev", origin="worker",
                      payload={"state": "busy", "last_action_ts": datetime.now(timezone.utc).isoformat()}))
    old = Orchestrator(state_dir, config, transport)
    registry = RoleSessionRegistry(state_dir / "role_sessions.yaml", project_root=str(state_dir.parent))
    old_session = str(registry.get_or_create("dev"))
    old._instance_state["dev"] = "recycling"
    old._set_worker_state("dev", "recycling", reason="context hard cap")
    del old
    transport.alive_flags["dev"] = False
    restarted = Orchestrator(state_dir, config, transport)
    restarted._dead_threshold = 1
    assert restarted._last_worker_state["dev"] == "recycling"
    assert restarted._instance_state == {}
    assert restarted._worker_liveness_stale(config.roles[0])[0] is False
    return restarted, transport, registry, old_session


@pytest.mark.parametrize("obligation", ["task", "fanout"])
def test_restarted_watchdog_recovers_dead_recycling_with_fresh_briefing(state_dir, obligation):
    orch, transport, registry, old_session = interrupted_runtime(state_dir, obligation=obligation)
    before = TaskStore(state_dir / "kanban.json").get("TASK-LIVENESS-DEV")
    original_operation = reduce_workflow_operations(EventLog(state_dir / "events.jsonl").read_all())["op-original"]
    decisions = orch._capture_logs()
    assert [d.action for d in decisions] == ["respawn"]
    assert transport.spawn_calls == ["dev"]
    assert transport.terminate_calls == ["dev"]
    assert transport.sent
    current_registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml", project_root=str(state_dir.parent),
    )
    assert current_registry.get("dev") is None  # Codex old context must not be resumed.
    assert "resume" not in transport.argv[0][1]
    events = EventLog(state_dir / "events.jsonl").read_all()
    recycled = next(e for e in events if e.type == "worker.recycling")
    assert recycled.payload["old_session"] == old_session
    assert recycled.payload["reason"] == "interrupted_context_recycle"
    if obligation == "task":
        assert "TASK-LIVENESS-DEV" in transport.sent[0][1].read_text()
        after = TaskStore(state_dir / "kanban.json").get("TASK-LIVENESS-DEV")
        assert after.active_dispatch_id == before.active_dispatch_id == "disp-dev"
    else:
        assert transport.sent[0][1] == state_dir / "briefings/reader.md"
        assert "fanout child briefing" in transport.sent[0][2]
        injected = next(e for e in events if e.type == "worker.recovery.injected")
        assert injected.payload["run_id"] == "reader-run"
    assert not any(e.type in {"task.assigned", "task.pipeline.stage.dispatched"} for e in events)
    recovered_operation = reduce_workflow_operations(events)["op-original"]
    for key in ("operation_id", "active_attempt_id", "dispatch_id", "lease_id", "status"):
        assert recovered_operation[key] == original_operation[key]
    assert orch._last_worker_state["dev"] == "busy"
    assert orch._instance_state["dev"] == "healthy"


@pytest.mark.parametrize("guard", ["alive", "terminal", "in_process", "blocked_human", "safe_halted"])
def test_restarted_recycle_preserves_watchdog_guards(state_dir, monkeypatch, guard):
    orch, transport, _, _ = interrupted_runtime(state_dir)
    if guard == "alive":
        transport.alive_flags["dev"] = True
    elif guard == "terminal":
        TaskStore(state_dir / "kanban.json").update("TASK-LIVENESS-DEV", status="done")
    elif guard == "in_process":
        orch._instance_state["dev"] = "recycling"
    elif guard == "blocked_human":
        orch._set_worker_state("dev", "blocked_human", reason="operator")
    else:
        monkeypatch.setattr(orch, "_runtime_safe_halted", lambda: True)
    orch._capture_logs()
    assert transport.spawn_calls == []
    assert transport.terminate_calls == []


def test_interrupted_recycle_honors_other_replacement_lease(state_dir):
    orch, transport, _, _ = interrupted_runtime(state_dir)
    with SessionLock(state_dir / "locks/respawns", "dev"):
        decisions = orch._capture_logs()
    assert [d.action for d in decisions] == ["respawn_in_progress"]
    assert transport.spawn_calls == []
    assert transport.terminate_calls == []
    # The next poll can recover once the owner has released its lease.
    assert [d.action for d in orch._capture_logs()] == ["respawn"]
    assert transport.spawn_calls == ["dev"]


@pytest.mark.parametrize("guard", ["terminal_reader", "cross_role"])
def test_interrupted_recycle_never_adopts_an_unowned_replacement(state_dir, monkeypatch, guard):
    orch, transport, _, _ = interrupted_runtime(state_dir, obligation="fanout")
    if guard == "terminal_reader":
        orch.event_writer.append(ZfEvent(type="fanout.child.completed", actor="dev", payload={
            "fanout_id": "fanout-read", "child_id": "scan", "run_id": "reader-run",
        }))
    elif guard == "cross_role":
        orch._set_worker_state("dev", "busy", reason="not recycling")
        orch._set_worker_state("another-role", "recycling", reason="other role")
    orch._capture_logs()
    assert transport.spawn_calls == []
    assert transport.terminate_calls == []


def test_failed_local_recycle_still_uses_existing_stale_recovery(state_dir, monkeypatch):
    orch, transport, _, _ = interrupted_runtime(state_dir)
    orch._instance_state["dev"] = "recycling"
    original_spawn = transport.spawn
    def failed_spawn(*args, **kwargs):
        raise RuntimeError("lost launcher")

    monkeypatch.setattr(transport, "spawn", failed_spawn)
    orch._start_recycle(orch.config.roles[0])
    assert any(e.type == "worker.recycle.failed" for e in orch.event_log.read_all())
    monkeypatch.setattr(transport, "spawn", original_spawn)
    monkeypatch.setattr(orch, "_worker_liveness_stale", lambda _: (True, "old heartbeat"))
    decisions = orch._capture_logs()
    assert [d.action for d in decisions] == ["respawn"]
    assert transport.spawn_calls == ["dev"]
    respawned = [e for e in orch.event_log.read_all() if e.type == "worker.respawned"]
    assert respawned[-1].payload["reason"] == "watchdog"


def test_run_once_recovers_hydrated_recycle_and_keeps_original_task(state_dir):
    orch, transport, _, _ = interrupted_runtime(state_dir)
    orch.run_once()
    assert transport.spawn_calls == ["dev"]
    assert transport.sent and "TASK-LIVENESS-DEV" in transport.sent[0][1].read_text()
    assert TaskStore(state_dir / "kanban.json").get("TASK-LIVENESS-DEV").active_dispatch_id == "disp-dev"


def test_interrupted_recycle_keeps_generation_owned_workspace(workspace):
    # Use a real current request/attempt/envelope rather than an unauthenticated
    # dispatch fragment; stronger placement gates must not be disabled for a fixture.
    runtime, role, task, root, _, _ = workspace
    role.backend = "codex"
    transport = RecordingTransport()
    old = Orchestrator(runtime.state_dir, runtime.config, transport)
    old._instance_state[role.instance_id] = "recycling"
    old._set_worker_state(role.instance_id, "recycling", reason="context hard cap")
    old.event_writer.append(ZfEvent(type="worker.heartbeat", actor=role.instance_id, origin="worker",
        payload={"state": "busy", "last_action_ts": datetime.now(timezone.utc).isoformat()}))
    del old
    candidate = root / "unfinished.txt"
    candidate.write_text("candidate work must survive\n")
    transport.alive_flags[role.instance_id] = False
    orch = Orchestrator(runtime.state_dir, runtime.config, transport)
    orch._dead_threshold = 1
    assert [d.action for d in orch._capture_logs()] == ["respawn"]
    assert transport.argv[0][2] == root.resolve()
    assert candidate.read_text() == "candidate work must survive\n"
    assert orch.task_store.get(task.id).active_dispatch_id == task.active_dispatch_id
    assert len([e for e in orch.event_log.read_all() if e.type == "task.pipeline.stage.dispatched"]) == 1
