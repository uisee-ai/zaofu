"""Cancelled runs cannot stop a reused worker or hide termination failures."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.store import TaskStore
from zf.core.task.schema import Task
from zf.runtime.run_cancel_reconciliation import _release_fanout_children
from zf.runtime.worker_state_runtime import WorkerStateRuntimeMixin


class Runtime(WorkerStateRuntimeMixin):
    pass


def _runtime(tmp_path: Path, *, newer_dispatch: bool = False, fail_stop: bool = False):
    runtime = Runtime()
    runtime.state_dir = tmp_path
    runtime.project_root = tmp_path
    runtime.event_log = EventLog(tmp_path / "events.jsonl")
    runtime.event_writer = EventWriter(runtime.event_log)
    runtime.task_store = TaskStore(tmp_path / "kanban.json")
    runtime.task_store.add(Task(id="TASK-1", title="scan", status="in_progress"))
    runtime._last_worker_state = {"scan": "busy"}
    runtime._last_worker_task_id = {"scan": "TASK-1"}
    stopped = []

    def terminate(role_name):
        stopped.append(role_name)
        if fail_stop:
            raise RuntimeError("provider still executing")

    runtime.transport = SimpleNamespace(terminate=terminate)
    for run_id in (["old", "new"] if newer_dispatch else ["old"]):
        runtime.event_writer.append(ZfEvent(
            type="fanout.child.dispatched", actor="zf-cli", task_id="TASK-1",
            payload={"role_instance": "scan", "run_id": run_id},
        ))
    return runtime, stopped


def _release(runtime):
    _release_fanout_children(
        runtime, cancellation=ZfEvent(type="run.cancelled", actor="operator"),
        fanout_id="fanout-old", reason="operator cancel",
        manifest={"trace_id": "workflow-old", "children": [{
            "role_instance": "scan", "run_id": "old", "task_id": "TASK-1",
            "child_id": "scan", "status": "dispatched",
        }]}, events=runtime.event_log.read_all(),
    )


def test_cancel_stops_read_only_fanout_with_shared_parent_task_and_is_idempotent(tmp_path):
    runtime, stopped = _runtime(tmp_path)
    _release(runtime)
    _release(runtime)
    assert stopped == ["scan"]
    assert runtime._last_worker_state["scan"] == "idle"


@pytest.mark.parametrize("different_task", [False, True])
def test_old_cancel_does_not_stop_or_release_reused_worker(tmp_path, different_task):
    runtime, stopped = _runtime(tmp_path, newer_dispatch=not different_task)
    if different_task:
        runtime._last_worker_task_id["scan"] = "TASK-NEW"
    _release(runtime)
    assert stopped == []
    assert runtime._last_worker_state["scan"] == "busy"
    assert runtime._last_worker_task_id["scan"] == ("TASK-NEW" if different_task else "TASK-1")


def test_stop_failure_stays_blocked_and_can_be_retried(tmp_path):
    runtime, stopped = _runtime(tmp_path, fail_stop=True)
    _release(runtime)
    assert stopped == ["scan"]
    assert runtime._last_worker_state["scan"] == "blocked_human"
    assert runtime._last_worker_task_id["scan"] == "TASK-1"
    runtime.transport = SimpleNamespace(terminate=lambda role: stopped.append(role))
    _release(runtime)
    assert stopped == ["scan", "scan"]
    assert runtime._last_worker_state["scan"] == "idle"


def test_latest_operation_dispatch_also_protects_a_reused_worker(tmp_path):
    runtime, stopped = _runtime(tmp_path)
    runtime.event_writer.append(ZfEvent(
        type="workflow.operation.started", actor="zf-cli", task_id="TASK-1",
        payload={"role_instance": "scan", "dispatch_id": "new-operation-dispatch"},
    ))
    _release(runtime)
    assert stopped == []
    assert runtime._last_worker_state["scan"] == "busy"


def test_replayed_busy_projection_does_not_terminate_an_already_stopped_dispatch(tmp_path):
    runtime, stopped = _runtime(tmp_path)
    _release(runtime)
    # A queued pre-cancel worker event can be consumed after reconciliation.
    runtime._last_worker_state["scan"] = "busy"
    runtime._last_worker_task_id["scan"] = "TASK-1"
    _release(runtime)
    assert stopped == ["scan"]
    assert not any(event.payload.get("to") == "blocked_human"
                   for event in runtime.event_log.read_all())


def test_stop_that_returns_with_live_provider_remains_blocked(tmp_path):
    runtime, stopped = _runtime(tmp_path)
    runtime.transport.is_alive = lambda role: True
    _release(runtime)
    assert stopped == ["scan"]
    assert runtime._last_worker_state["scan"] == "blocked_human"
    assert runtime._last_worker_task_id["scan"] == "TASK-1"
