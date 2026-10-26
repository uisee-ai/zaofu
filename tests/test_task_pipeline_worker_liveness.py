from pathlib import Path

import pytest

from tests.test_capture_logs_watchdog import _AliveControllableTransport
from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


def runtime_case(tmp_path: Path, *, status: str):
    state = tmp_path / "state"
    state.mkdir()
    (state / "memory").mkdir()
    SessionStore(state / "session.yaml").create(project_root=str(tmp_path))
    TaskStore(state / "kanban.json").add(Task(
        id="T1", status="in_progress", assigned_to="verify-1",
        active_dispatch_id="dispatch-1",
    ))
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    identity = dict(operation_id="op-1", workflow_run_id="run-1", task_id="T1",
                    role_instance="verify-1", task_pipeline_stage="verify",
                    dispatch_id="dispatch-1", task_map_generation="map-1")
    writer.append(ZfEvent(type="workflow.operation.requested", task_id="T1", payload=identity))
    writer.append(ZfEvent(type="workflow.operation.started", task_id="T1", payload=identity))
    writer.append(ZfEvent(type="task.dispatched", task_id="T1", payload={**identity, "assignee": "verify-1"}))
    if status != "running":
        writer.append(ZfEvent(type=f"workflow.operation.{status}", task_id="T1", payload=identity))
    cfg = ZfConfig(project=ProjectConfig(name="liveness"),
                   session=SessionConfig(tmux_session="liveness"),
                   roles=[RoleConfig(name="verify", instance_id="verify-1", backend="mock")])
    transport = _AliveControllableTransport()
    runtime = Orchestrator(state, cfg, transport)
    return runtime, transport, writer, identity


@pytest.mark.parametrize("status", ["settled", "blocked", "failed", "cancelled", "superseded"])
@pytest.mark.parametrize("alive", [True, False])
def test_terminal_stage_is_not_a_worker_obligation(tmp_path, monkeypatch, status, alive):
    runtime, transport, _, _ = runtime_case(tmp_path, status=status)
    transport.alive_flags["verify-1"] = alive
    monkeypatch.setattr(runtime, "_worker_liveness_stale", lambda role: (True, "old"))
    runtime._dead_threshold = 1
    assert runtime._active_task_for_instance("verify-1") is None
    runtime._capture_logs()
    assert transport.spawn_calls == []
    assert transport.terminate_calls == []
    assert runtime.task_store.get("T1").status == "in_progress"
    assert not any(e.type in {"worker.stuck", "task.requeued"} for e in runtime.event_log.read_all())


def test_new_running_stage_invalidates_terminal_cache(tmp_path):
    runtime, _, writer, identity = runtime_case(tmp_path, status="settled")
    assert runtime._active_task_for_instance("verify-1") is None
    identity = {**identity, "operation_id": "op-2", "dispatch_id": "dispatch-2"}
    writer.append(ZfEvent(type="workflow.operation.requested", task_id="T1", payload=identity))
    writer.append(ZfEvent(type="workflow.operation.started", task_id="T1", payload=identity))
    writer.append(ZfEvent(type="task.dispatched", task_id="T1", payload={**identity, "assignee": "verify-1"}))
    runtime.task_store.update("T1", active_dispatch_id="dispatch-2")
    assert runtime._active_task_for_instance("verify-1").id == "T1"


def test_running_stage_remains_a_worker_obligation(tmp_path):
    runtime, _, _, _ = runtime_case(tmp_path, status="running")
    assert runtime._active_task_for_instance("verify-1").id == "T1"


def test_later_legacy_assignment_is_not_hidden_by_terminal_pipeline(tmp_path):
    runtime, _, writer, _ = runtime_case(tmp_path, status="settled")
    assert runtime._active_task_for_instance("verify-1") is None
    writer.append(ZfEvent(type="task.dispatched", task_id="T1", payload={
        "assignee": "verify-1", "dispatch_id": "legacy-dispatch",
    }))
    runtime.task_store.update("T1", active_dispatch_id="legacy-dispatch")
    assert runtime._active_task_for_instance("verify-1").id == "T1"
