"""No interactive monitor: failed lease -> RM -> mock transport -> WRC replay.

This proves the recovery handoff; provider repair quality and host supervision
are separate acceptance tiers.
"""
import pytest

from tests.test_task_pipeline_fault_matrix import _runtime, _running_operation_v2_attempt
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.task_attempt_runtime import reconcile_task_attempts
from zf.runtime.task_pipeline_recovery import reconcile_task_pipeline_redrives
from zf.runtime.workflow_operation import reduce_workflow_operations

pytestmark = pytest.mark.mock_e2e


def test_expired_attempt_reaches_wrc_redrive_without_monitor_session(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    attempt = _running_operation_v2_attempt(runtime)
    monkeypatch.setattr("zf.runtime.task_attempt_runtime._expired", lambda _: True)
    reconcile_task_attempts(runtime)
    run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer,
                     config=runtime.config, project_root=tmp_path, spawn_repairs=False)
    requests = [e for e in runtime.event_log.read_all() if e.type == "worker.respawn.requested"]
    assert len(requests) == 1
    assert requests[0].payload["attempt_id"] == attempt["attempt_id"]
    # Mock transport's durable outcome survives loss of the wake notification.
    runtime.event_writer.emit("worker.respawn.completed", actor="impl-1", task_id="TASK-A",
        causation_id=requests[0].id, payload={"reason": "mock transport recovered"})
    # New ledger reader models a control-plane restart, with no chat memory.
    runtime.event_log = EventLog(runtime.state_dir / "events.jsonl")
    runtime.event_writer = EventWriter(runtime.event_log)
    contexts = {"TASK-A": {"workflow_run_id": "run-1"}}
    assert len(reconcile_task_pipeline_redrives(runtime, generation_contexts=contexts)) == 1
    assert reconcile_task_pipeline_redrives(runtime, generation_contexts=contexts) == []
    op = reduce_workflow_operations(runtime.event_log.read_all())[attempt["operation_id"]]
    assert op["status"] == "requested" and op["redrive_count"] == 1
    assert op["operation_id"] == attempt["operation_id"]
    runtime.event_writer.emit("simulation.done", payload={"scenario": "design199-recovery"})
