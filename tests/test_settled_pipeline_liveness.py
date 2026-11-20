from zf.core.events.model import ZfEvent
from zf.core.events.log import EventLog
from zf.runtime.workflow_operation import WorkflowOperationService
from zf.core.events.writer import EventWriter
from zf.runtime.task_completion_liveness import TaskCompletionLivenessMixin
from tests.test_orphan_timeout import _seed_busy_task, state_dir, config, transport
from zf.runtime.orchestrator import Orchestrator
import pytest


@pytest.mark.parametrize("verdict", ["abstained", "blocked", "passed"])
def test_admitted_pipeline_result_waiting_for_next_owner_is_not_orphaned(
    state_dir, config, transport, verdict
):
    orch = Orchestrator(state_dir, config, transport)
    _seed_busy_task(state_dir, "T1", 0)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    svc = WorkflowOperationService(
        state_dir=state_dir, event_log=log, event_writer=writer
    )
    req = svc.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-1",
        operation_type="task-stage",
        request={"task_pipeline_stage": "verify", "operation_generation": 1},
        task_id="T1",
    )
    writer.append(
        ZfEvent(
            type="task.dispatched",
            task_id="T1",
            payload={"operation_id": "op-1", "dispatch_id": "d1"},
        )
    )
    svc.mark_started(
        operation_id="op-1",
        request_hash=req.request_hash,
        workflow_run_id="run-1",
        task_id="T1",
        dispatch_id="d1",
    )
    writer.append(
        ZfEvent(
            type="workflow.call.result.admitted",
            task_id="T1",
            payload={
                "operation_id": "op-1",
                "semantic_verdict": verdict,
                "control_result_ref": {"ref": "result", "sha256": "a" * 64},
            },
        )
    )
    svc.settle(
        operation_id="op-1",
        request_hash=req.request_hash,
        workflow_run_id="run-1",
        task_id="T1",
        admitted_call_result_ref={"ref": "result", "sha256": "a" * 64},
    )
    orch._dispatch_epoch["T1"] = 0
    orch._now = lambda: 4000
    orch._check_orphaned_tasks()
    assert not any(e.type == "task.orphaned" for e in log.read_all())
    assert orch.task_store.get("T1").status == "in_progress"
    # A new delivery cannot inherit the previous completion's liveness.
    writer.append(
        ZfEvent(
            type="task.dispatched",
            task_id="T1",
            payload={"operation_id": "op-2", "dispatch_id": "d2"},
        )
    )
    assert not orch._task_has_admitted_writer_completion("T1")
