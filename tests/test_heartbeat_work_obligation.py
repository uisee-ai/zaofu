"""An idle on-demand Verify must not turn a stale busy mirror into safe-halt."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_capture_logs_watchdog import _AliveControllableTransport
from tests.test_stuck_dedup import state_dir
from zf.core.config.schema import ProjectConfig, RoleConfig, RoleLifecycleConfig, ZfConfig
from zf.core.events import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.runtime.orchestrator import Orchestrator


@pytest.fixture
def runtime(state_dir):
    role = RoleConfig(name="verify", instance_id="verify-1", backend="mock",
                      stuck_threshold_seconds=300,
                      lifecycle=RoleLifecycleConfig(mode="on_demand"))
    transport = _AliveControllableTransport()
    orch = Orchestrator(state_dir, ZfConfig(project=ProjectConfig(name="test"), roles=[role]), transport)
    reg = RoleSessionRegistry(state_dir / "role_sessions.yaml", project_root=str(state_dir.parent))
    reg.get_or_create(role.instance_id)
    return orch, role, reg, transport


def heartbeat(reg, *, task_id="", age=360):
    reg.record_heartbeat("verify-1", {"state": "busy", "current_task_id": task_id})
    reg._meta["verify-1"]["last_heartbeat_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=age)
    ).isoformat()
    reg._save()


def operation(orch, *, status="running", owner="verify-1", op_id="op-verify"):
    identity = {"operation_id": op_id, "workflow_run_id": "run-product",
                "operation_type": "task-stage", "task_pipeline_stage": "verify",
                "task_id": "MEDIA", "role_instance": owner,
                "active_attempt_id": "attempt-verify", "dispatch_id": "dispatch-verify"}
    for kind in ["requested", "started"] + ([] if status == "running" else [status]):
        orch.event_writer.append(ZfEvent(type=f"workflow.operation.{kind}", actor="kernel",
                                        payload=identity | {"semantic_verdict": "passed"}))


def test_settled_verify_empty_heartbeat_never_respawns_or_safe_halts(runtime):
    """Incident: passed 06:20; stale empty-task busy heartbeats later hit circuit."""
    orch, role, reg, transport = runtime
    operation(orch, status="settled")
    heartbeat(reg)
    orch._respawn_success_circuit_opened.add(role.instance_id)
    orch._run_heartbeat_sweep()
    types = [event.type for event in orch.event_log.read_all()]
    assert "worker.stuck" not in types
    assert "runtime.safe_halted" not in types
    assert "dispatch.paused" not in types
    assert not transport.spawn_calls


@pytest.mark.parametrize("age", [120, 360])
def test_empty_on_demand_worker_has_no_silent_or_stuck_signal(runtime, age):
    orch, _, reg, _ = runtime
    heartbeat(reg, age=age)
    orch._run_heartbeat_sweep()
    assert not any(e.type in {"worker.probe.silent", "worker.stuck"} for e in orch.event_log.read_all())


@pytest.mark.parametrize("status", ["settled", "cancelled", "blocked", "superseded"])
def test_terminal_operation_does_not_keep_on_demand_worker_busy(runtime, status):
    orch, _, reg, _ = runtime
    operation(orch, status=status)
    heartbeat(reg)
    assert orch._heartbeat_current_task_still_owned(reg, "verify-1") is False


@pytest.mark.parametrize("task_id", ["", "MEDIA"])
def test_active_typed_reader_owns_work_even_when_parent_is_backlog(runtime, task_id):
    orch, _, reg, _ = runtime
    orch.task_store.add(Task(id="MEDIA", title="reader parent", status="backlog"))
    operation(orch)
    heartbeat(reg, task_id=task_id)
    assert orch._heartbeat_current_task_still_owned(reg, "verify-1") is True


def test_other_replica_operation_does_not_own_this_instance(runtime):
    orch, _, reg, _ = runtime
    operation(orch, owner="verify-2")
    heartbeat(reg)
    assert orch._heartbeat_current_task_still_owned(reg, "verify-1") is False


@pytest.mark.parametrize("mode", ["resident", "eager"])
def test_resident_and_eager_control_roles_keep_legacy_heartbeat_detection(runtime, mode):
    orch, role, reg, _ = runtime
    role.lifecycle = RoleLifecycleConfig(mode=mode)
    heartbeat(reg)
    assert orch._heartbeat_current_task_still_owned(reg, "verify-1") is True


def test_legacy_writer_still_owns_its_task(runtime):
    orch, _, reg, _ = runtime
    orch.task_store.add(Task(id="MEDIA", title="legacy", status="in_progress",
                             assigned_to="verify-1", active_dispatch_id="legacy-dispatch"))
    heartbeat(reg, task_id="MEDIA")
    assert orch._heartbeat_current_task_still_owned(reg, "verify-1") is True


@pytest.mark.parametrize("terminal", [False, True])
def test_legacy_reader_child_is_an_obligation_until_its_result(runtime, terminal):
    orch, _, reg, _ = runtime
    child = {"fanout_id": "fanout-read", "child_id": "scan", "run_id": "reader-run",
             "role_instance": "verify-1", "task_id": "PARENT"}
    orch.event_writer.append(ZfEvent(type="fanout.child.dispatched", actor="kernel", payload=child))
    if terminal:
        orch.event_writer.append(ZfEvent(type="fanout.child.completed", actor="verify-1", payload=child))
    heartbeat(reg)
    assert orch._heartbeat_current_task_still_owned(reg, "verify-1") is (not terminal)


def test_unknown_event_read_preserves_detection(runtime, monkeypatch):
    orch, _, reg, _ = runtime
    heartbeat(reg)
    def unavailable():
        raise OSError("ledger unavailable")
    monkeypatch.setattr(orch.event_log, "read_all", unavailable)
    assert orch._heartbeat_current_task_still_owned(reg, "verify-1") is True


def test_direct_stuck_entrypoint_ignores_confirmed_idle_on_demand(runtime):
    orch, role, _, transport = runtime
    result = orch._report_stuck_worker(role, source="pane_watchdog")
    assert result.action == "noop"
    assert not transport.spawn_calls
    assert not any(e.type == "worker.stuck" for e in orch.event_log.read_all())


@pytest.mark.parametrize("kind", ["typed_reader", "legacy_task", "legacy_fanout"])
def test_sweep_still_reports_stuck_for_real_obligations(runtime, monkeypatch, kind):
    orch, _, reg, _ = runtime
    if kind == "typed_reader":
        operation(orch)
    elif kind == "legacy_task":
        orch.task_store.add(Task(id="MEDIA", title="busy", status="in_progress", assigned_to="verify-1"))
    else:
        orch.event_writer.append(ZfEvent(type="fanout.child.dispatched", payload={
            "fanout_id": "f", "child_id": "c", "run_id": "reader-run", "role_instance": "verify-1",
        }))
    heartbeat(reg)
    # Recovery itself is tested separately; this assertion exercises the sweep's
    # ownership, finished-dispatch and provider-turn guards together.
    reports = []
    monkeypatch.setattr(orch, "_report_stuck_worker", lambda role, **kw: reports.append(kw))
    orch._run_heartbeat_sweep()
    assert len(reports) == 1
    assert reports[0]["source"] == "heartbeat_sweep"


@pytest.mark.parametrize("status,expected", [(None, True), ("settled", False), ("cancelled", False)])
def test_task_attempt_delivery_is_owned_unless_typed_operation_has_terminated(runtime, status, expected):
    orch, _, reg, _ = runtime
    if status:
        operation(orch, status=status)
    now = datetime.now(timezone.utc).isoformat()
    TaskAttemptStore(orch.state_dir / "task_attempts.json").ensure_for_dispatch(
        run_id="run-product", task_id="MEDIA", dispatch_id="dispatch-verify",
        role="verify", instance_id="verify-1", operation_id="op-verify",
        briefing_ref="briefings/verify.md", created_at=now, lease_expires_at=now,
        max_attempts=3,
    )
    heartbeat(reg)
    assert orch._heartbeat_current_task_still_owned(reg, "verify-1") is expected


def test_typed_terminal_reader_overrides_unclosed_legacy_dispatch(runtime):
    orch, _, reg, _ = runtime
    orch.event_writer.append(ZfEvent(type="fanout.child.dispatched", payload={
        "fanout_id": "f", "child_id": "c", "run_id": "attempt-verify", "role_instance": "verify-1",
    }))
    operation(orch, status="cancelled")
    heartbeat(reg)
    assert orch._heartbeat_current_task_still_owned(reg, "verify-1") is False


@pytest.mark.parametrize("store", ["task", "attempt"])
def test_store_read_failure_does_not_disable_stuck_detection(runtime, monkeypatch, store):
    orch, _, reg, _ = runtime
    heartbeat(reg)
    def unavailable(*args):
        raise OSError("store unavailable")
    if store == "task":
        monkeypatch.setattr(orch.task_store, "list_all", unavailable)
    else:
        monkeypatch.setattr(TaskAttemptStore, "current_rows", unavailable)
    assert orch._heartbeat_current_task_still_owned(reg, "verify-1") is True
