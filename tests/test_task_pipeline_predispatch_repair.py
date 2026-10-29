from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.task_pipeline_predispatch_repair import reconcile_missing_flow_kind_requests
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.workflow_operation import reduce_workflow_operations
from tests.test_task_pipeline_reconciler import _policy


@pytest.mark.parametrize("attempted,mode,should_repair", [
    (False, "off", True), (True, "off", False), (False, "blocking", False),
])
def test_repair_only_unsent_known_missing_flow_request(
    tmp_path, monkeypatch, attempted, mode, should_repair,
):
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    identity = {"workflow_run_id": "run-1", "task_id": "T1",
                "task_map_generation": "map-1", "task_pipeline_stage": "impl",
                "operation_generation": 1, "operation_id": "op-1"}
    ref = write_immutable_json_sidecar(
        tmp_path, {"operation_id": "op-1", "task_id": "T1", "workflow_run_id": "run-1",
                   "request": {**identity, "semantic_result_submit_mode": mode,
                               "result_identity": {}}},
        root="operations/requests", kind="workflow_operation_request",
        schema_version="workflow-operation-request.v1", created_by="test",
    )
    writer.append(ZfEvent(type="workflow.operation.requested", task_id="T1",
                          payload={**identity, "request_hash": "a" * 64,
                                   "request_ref": ref}))
    failure = writer.append(ZfEvent(
        type="task.pipeline.stage.dispatch_deferred", task_id="T1", origin="kernel", actor="orchestrator",
        payload={**identity, "reason": "ValueError: Task Pipeline impl requires a semantic result submit command"},
    ))
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_predispatch_repair.task_attempt_store",
        lambda runtime: SimpleNamespace(current_rows=lambda: (
            [{"operation_id": "op-1", "status": "sent"}] if attempted else []
        )),
    )
    runtime = SimpleNamespace(state_dir=tmp_path, event_log=log, event_writer=writer)
    contexts = {"T1": {"workflow_run_id": "run-1", "task_map_generation": "map-1",
                       "flow_kind": "prd"}}
    reconcile_missing_flow_kind_requests(runtime, generation_contexts=contexts)
    reconcile_missing_flow_kind_requests(runtime, generation_contexts=contexts)
    events = log.read_all()
    superseded = [e for e in events if e.type == "workflow.operation.superseded"]
    assert len(superseded) == int(should_repair)
    if should_repair:
        assert superseded[0].causation_id == failure.id
        operations = list(reduce_workflow_operations(events).values())
        projection = TaskPipelineReconciler().reconcile(
            policy=_policy(), tasks=[{"id": "T1", "status": "backlog"}],
            operations=operations, attempts=[],
        )
        assert projection["dispatchable"]["impl"][0]["operation_generation"] == "2"


def test_predispatch_repair_does_not_consume_semantic_rework_budget():
    policy = _policy()
    policy["max_rework_attempts"] = 2
    operations = [
        {"task_id": "T1", "operation_id": "old", "task_pipeline_stage": "impl",
         "operation_generation": 1, "status": "superseded",
         "reason": "missing_flow_kind_before_dispatch"},
        {"task_id": "T1", "operation_id": "impl-3", "task_pipeline_stage": "impl",
         "operation_generation": 3, "status": "settled", "semantic_verdict": "passed"},
        {"task_id": "T1", "operation_id": "verify-3", "task_pipeline_stage": "verify",
         "operation_generation": 3, "status": "settled", "semantic_verdict": "rejected"},
    ]
    projection = TaskPipelineReconciler().reconcile(
        policy=policy, tasks=[{"id": "T1", "status": "backlog"}],
        operations=operations, attempts=[],
    )
    assert projection["dispatchable"]["impl"][0]["operation_generation"] == "4"
