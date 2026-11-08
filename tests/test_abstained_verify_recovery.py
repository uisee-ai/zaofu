import hashlib
import json

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)
from zf.runtime.workflow_resume import build_workflow_operation_resume_checkpoints
from zf.runtime.workflow_resume_apply import _apply_operation_checkpoint


def seed(root, verdict="abstained"):
    store = TaskStore(root / "kanban.json")
    store.add(Task(id="T1", title="test", status="in_progress"))
    log = EventLog(root / "events.jsonl")
    writer = EventWriter(log)
    svc = WorkflowOperationService(state_dir=root, event_log=log, event_writer=writer)
    identities = {}
    for stage in ["impl", "verify"]:
        op = "op-" + stage
        req = svc.ensure_operation(
            workflow_run_id="run-1",
            operation_id=op,
            operation_type="task-stage",
            task_id="T1",
            request={
                "task_pipeline_stage": stage,
                "task_map_generation": "map-1",
                "operation_generation": 1,
            },
        )
        identities[stage] = req.request_hash
        svc.mark_started(
            operation_id=op,
            request_hash=req.request_hash,
            workflow_run_id="run-1",
            task_id="T1",
            dispatch_id="dispatch-" + stage,
            active_attempt_id="attempt-" + stage,
        )
        source = writer.append(
            ZfEvent(
                type="dev.build.done"
                if stage == "impl"
                else "task.pipeline.verify.failed",
                task_id="T1",
                payload={
                    "operation_id": op,
                    "source_commit": "a" * 40,
                    "target_commit": "a" * 40,
                },
            )
        )
        body = {
            "operation_id": op,
            "request_hash": req.request_hash,
            "workflow_run_id": "run-1",
            "task_id": "T1",
            "operation_generation": 1,
            "target_commit": "a" * 40,
            "execution_status": "failed" if stage == "verify" else "completed",
            "verdict": verdict if stage == "verify" else "passed",
        }
        path = root / "artifacts" / f"{op}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(body))
        ref = {
            "ref": str(path.relative_to(root)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_event_id": source.id,
        }
        writer.append(
            ZfEvent(
                type="workflow.call.result.admitted",
                task_id="T1",
                payload={
                    "operation_id": op,
                    "semantic_verdict": body["verdict"],
                    "control_result_ref": ref,
                },
            )
        )
        svc.settle(
            operation_id=op,
            request_hash=req.request_hash,
            workflow_run_id="run-1",
            task_id="T1",
            admitted_call_result_ref=ref,
        )
    return log, writer, svc, store, identities


def test_same_target_recovery_and_second_settlement(tmp_path):
    log, writer, svc, store, ids = seed(tmp_path)
    rows = build_workflow_operation_resume_checkpoints(tmp_path)
    assert len(rows) == 1
    assert rows[0].safe_resume_action == "redrive_abstained_task_verification"
    result = _apply_operation_checkpoint(store, writer, rows[0], state_dir=tmp_path)
    assert result.applied
    op = reduce_workflow_operations(log.read_all())["op-verify"]
    assert op["status"] == "requested" and op["request_hash"] == ids["verify"]
    assert op["operation_generation"] == 1 and op["verification_recovery_count"] == 1
    assert not op["admitted_control_result_ref"] and not op["semantic_verdict"]
    assert not _apply_operation_checkpoint(
        store, writer, rows[0], state_dir=tmp_path
    ).applied
    svc.mark_started(
        operation_id="op-verify",
        request_hash=ids["verify"],
        workflow_run_id="run-1",
        task_id="T1",
        dispatch_id="dispatch-new",
        active_attempt_id="attempt-new",
    )
    writer.append(
        ZfEvent(
            type="workflow.call.result.admitted",
            task_id="T1",
            payload={
                "operation_id": "op-verify",
                "semantic_verdict": "passed",
                "control_result_ref": {"ref": "new", "sha256": "b" * 64},
            },
        )
    )
    svc.settle(
        operation_id="op-verify",
        request_hash=ids["verify"],
        workflow_run_id="run-1",
        task_id="T1",
        admitted_call_result_ref={"ref": "new", "sha256": "b" * 64},
    )
    final = reduce_workflow_operations(log.read_all())["op-verify"]
    assert final["status"] == "settled" and final["semantic_verdict"] == "passed"
    assert (
        len(
            [
                e
                for e in log.read_all()
                if e.type == "workflow.operation.settled"
                and e.payload["operation_id"] == "op-verify"
            ]
        )
        == 2
    )
    assert build_workflow_operation_resume_checkpoints(tmp_path) == []


@pytest.mark.parametrize(
    "fault", ["passed", "rejected", "corrupt", "target", "done", "terminal"]
)
def test_recovery_cannot_reopen_other_outcomes_or_invalid_identity(tmp_path, fault):
    log, writer, svc, store, ids = seed(
        tmp_path, fault if fault in {"passed", "rejected"} else "abstained"
    )
    if fault == "corrupt":
        (tmp_path / "artifacts/op-verify.json").write_text("{}")
    if fault == "target":
        body = json.loads((tmp_path / "artifacts/op-verify.json").read_text())
        body["target_commit"] = "b" * 40
        path = tmp_path / "artifacts/op-verify.json"
        path.write_text(json.dumps(body))
        writer.append(
            ZfEvent(
                type="workflow.call.result.admitted",
                payload={
                    "operation_id": "op-verify",
                    "semantic_verdict": "abstained",
                    "control_result_ref": {
                        "ref": "artifacts/op-verify.json",
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    },
                },
            )
        )
    if fault == "done":
        store.update("T1", status="done")
    if fault == "terminal":
        writer.append(
            ZfEvent(
                type="run.failed",
                payload={"workflow_run_id": "run-1", "run_id": "run-1"},
            )
        )
    assert build_workflow_operation_resume_checkpoints(tmp_path) == []


def test_service_rejects_invented_decision_and_late_attempt(tmp_path):
    from zf.runtime.workflow_operation import WorkflowOperationError
    from tests.test_workflow_operation_resume import _seed_live_retry

    log, writer, svc, store, ids = seed(tmp_path)
    rows = build_workflow_operation_resume_checkpoints(tmp_path)
    with pytest.raises(WorkflowOperationError):
        svc.admit_redrive(
            operation_id="op-verify",
            request_hash=ids["verify"],
            workflow_run_id="run-1",
            task_id="T1",
            source_attempt_id="attempt-verify",
            recovery_decision_event_id="invented",
            recovery_decision_owner="kernel_replay",
            reason="retry",
        )
    _seed_live_retry(tmp_path, operation_id="op-verify", task_id="T1")
    assert not _apply_operation_checkpoint(
        store, writer, rows[0], state_dir=tmp_path
    ).applied
    assert build_workflow_operation_resume_checkpoints(tmp_path) == []


def test_second_abstention_does_not_grant_another_retry(tmp_path):
    log, writer, svc, store, ids = seed(tmp_path)
    row = build_workflow_operation_resume_checkpoints(tmp_path)[0]
    assert _apply_operation_checkpoint(store, writer, row, state_dir=tmp_path).applied
    svc.mark_started(
        operation_id="op-verify",
        request_hash=ids["verify"],
        workflow_run_id="run-1",
        task_id="T1",
        dispatch_id="second",
        active_attempt_id="second",
    )
    writer.append(
        ZfEvent(
            type="workflow.call.result.admitted",
            task_id="T1",
            payload={
                "operation_id": "op-verify",
                "semantic_verdict": "abstained",
                "control_result_ref": {"ref": "second", "sha256": "c" * 64},
            },
        )
    )
    svc.settle(
        operation_id="op-verify",
        request_hash=ids["verify"],
        workflow_run_id="run-1",
        task_id="T1",
        admitted_call_result_ref={"ref": "second", "sha256": "c" * 64},
    )
    assert build_workflow_operation_resume_checkpoints(tmp_path) == []
