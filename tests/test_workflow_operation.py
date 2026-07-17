from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
    stable_operation_id,
)


def _service(tmp_path: Path) -> WorkflowOperationService:
    log = EventLog(tmp_path / "events.jsonl")
    return WorkflowOperationService(
        state_dir=tmp_path,
        event_log=log,
        event_writer=EventWriter(log),
    )


def test_ensure_operation_dedupes_and_fails_closed_on_drift(tmp_path: Path) -> None:
    service = _service(tmp_path)
    operation_id = stable_operation_id(
        workflow_run_id="run-1",
        parent_stage_id="verify",
        operation_key="security",
    )
    first = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="agent",
        request={"prompt": "review", "dispatch_id": "volatile-1"},
        parent_stage_id="verify",
        task_id="T1",
    )
    replay = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="agent",
        request={"prompt": "review", "dispatch_id": "volatile-2"},
        parent_stage_id="verify",
        task_id="T1",
    )
    divergent = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="agent",
        request={"prompt": "different"},
        parent_stage_id="verify",
        task_id="T1",
    )
    assert first.created is True
    assert replay.replay_hit is True
    assert replay.request_hash == first.request_hash
    assert divergent.status == "divergent"
    events = service.event_log.read_all()
    assert sum(event.type == "workflow.operation.requested" for event in events) == 1
    assert sum(event.type == "workflow.operation.blocked" for event in events) == 1
    view = reduce_workflow_operations(events)[operation_id]
    assert view["request_count"] == 1
    assert view["replay_count"] == 0


def test_operation_settles_even_when_product_verdict_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    operation_id = "op-rejected"
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="agent",
        request={"prompt": "verify"},
        task_id="T1",
    )
    envelope_ref = {
        "ref_schema_version": "sidecar-ref.v1",
        "kind": "call_result_envelope",
        "ref": "artifacts/call-results/rejected.json",
        "sha256": "a" * 64,
    }
    service.settle(
        operation_id=operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="T1",
        admitted_call_result_ref=envelope_ref,
    )
    view = reduce_workflow_operations(service.event_log.read_all())[operation_id]
    assert view["status"] == "settled"
    assert view["admitted_call_result_ref"]["ref"].endswith("rejected.json")
