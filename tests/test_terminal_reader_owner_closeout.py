"""A terminal aggregate must not retain a superseded live Reader owner."""
import pytest

from zf.core.events import EventWriter, ZfEvent
from zf.runtime.workflow_operation import load_workflow_operation, WorkflowOperationService
from tests.test_reader_fanout_runtime import _durable_reader_state


@pytest.mark.parametrize("proof", ["admitted", "unadmitted", "other_run", "external"])
def test_terminal_reader_releases_only_after_admitted_replacement(tmp_path, monkeypatch, proof):
    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path, rework_source=True)
    writer = EventWriter(log, default_origin="kernel")
    op_id = child.get("operation_id") or child["payload"]["operation_id"]
    op = load_workflow_operation(log, op_id)
    owner = WorkflowOperationService(state_dir=state, event_log=log, event_writer=writer)
    owner.interrupt(operation_id=op_id, request_hash=op["request_hash"], workflow_run_id="run-durable",
                  task_id=op["task_id"], reason="graceful_stop")
    terminal = writer.append(ZfEvent(type="fanout.aggregate.completed", correlation_id="run-durable",
        payload={"fanout_id": child["fanout_id"], "status": "failed", "reason": "mixed_failure"}))
    source = next(e for e in log.read_all() if e.type == "dev.blocked")
    ready = writer.append(ZfEvent(type="task_map.ready", correlation_id="run-durable", payload={
        "workflow_run_id": "run-durable", "rework_of": source.id,
        "rework_summary": {"gap_tasks": [{"task_id": "T-NEW", "supersedes_task_ids": ["T-VERIFY"]}]}}))
    if proof != "unadmitted":
        admitted = writer.append(ZfEvent(type="task.pipeline.generation.admitted", correlation_id="run-durable",
            origin="external" if proof == "external" else "kernel", payload={
                "schema_version": "task-pipeline-generation.v1", "workflow_run_id": "other" if proof == "other_run" else "run-durable",
                "trigger_event_id": ready.id, "task_ids": ["T-NEW"]}))
    stops = []
    monkeypatch.setattr(transport, "terminate", lambda role: stops.append(role), raising=False)
    monkeypatch.setattr(transport, "is_alive", lambda role: role not in stops)
    runtime.run_once(events=[])
    result = load_workflow_operation(log, op_id)
    if proof == "admitted":
        assert result["status"] == "cancelled"
        cancel = next(e for e in log.read_all() if e.type == "workflow.operation.cancelled"
                      and e.payload.get("operation_id") == op_id)
        assert cancel.causation_id == admitted.id
        assert stops == ["verify-1"]
    else:
        assert result["status"] == "suspended"
        assert not stops
    assert not any(e.type == "fanout.cancelled" for e in log.read_all())
    assert runtime._fanout_manifest(child["fanout_id"])["aggregate"]["status"] == "failed"
    assert any(e.id == terminal.id for e in log.read_all())
    runtime.run_once(events=[])
    assert len(stops) == (1 if proof == "admitted" else 0)
    assert len(transport.sent) == 1
