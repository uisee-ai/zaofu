"""An admitted stop/resume checkpoint must reach the actual Reader start."""
from dataclasses import replace

import pytest

from tests.test_reader_fanout_runtime import _durable_reader_state
from zf.core.events import ZfEvent
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.reader_input_dispatch import reader_input_dispatch_matches
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_resume_apply import apply_workflow_resume


def checkpoint(tmp_path, prior_retry=False):
    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path)
    runtime.event_writer.emit("run.started", payload={"workflow_run_id": "run-durable"})
    if prior_retry:
        closed = runtime.event_writer.emit("provider.turn.closed", actor="verify-1",
            payload={"backend": "codex", "turn_id": "old", "provider_status": "completed"})
        runtime.run_once(events=[closed])
    op_id = child["payload"]["operation_id"]
    op = reduce_workflow_operations(log.read_all())[op_id]
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=runtime.event_writer)
    service.interrupt(operation_id=op_id, request_hash=op["request_hash"],
        workflow_run_id=op["workflow_run_id"], task_id=op["task_id"], reason="graceful_stop")
    projection = build_workflow_resume_projection(state, config)
    item = next(c for c in projection["operation_checkpoints"] if c["operation_id"] == op_id)
    assert item["safe_resume_action"] == "redrive_interrupted_reader_operation"
    assert apply_workflow_resume(state, config, checkpoint_id=item["checkpoint_id"])["applied"] == 1
    return state, log, transport, runtime, config, op_id


@pytest.mark.parametrize("prior_retry", [False, True])
def test_runtime_consumes_exact_checkpoint_without_spending_transport_retry(tmp_path, prior_retry):
    state, log, transport, runtime, _, op_id = checkpoint(tmp_path, prior_retry)
    before = log.read_all()
    old = reduce_workflow_operations(before)[op_id]
    redrive = next(e for e in reversed(before) if e.type == "workflow.operation.redrive_admitted")
    sent = len(transport.sent)
    launch = runtime.event_writer.emit("worker.launch_artifact.written",
        payload={"instance_id": "verify-1", "launch_attempt": 3})
    runtime.run_once(events=[launch])
    runtime.run_once(events=[])  # The registered periodic sweep owns Reader recovery.
    events = log.read_all()
    current = reduce_workflow_operations(events)[op_id]
    assert current["status"] == "running"
    assert len(transport.sent) == sent + 1
    assert all(current[k] == old[k] for k in ("operation_id", "active_attempt_id", "request_hash", "lease_id"))
    started = next(e for e in reversed(events) if e.type == "workflow.operation.started"
                   and e.payload["operation_id"] == op_id)
    assert started.causation_id == redrive.payload["recovery_decision_event_id"]
    manifest = load_attempt_source_manifest(state, current["active_attempt_id"])
    assert reader_input_dispatch_matches(current, manifest, events)
    assert sum(e.type == "fanout.child.dispatch_lost" for e in events) == sum(
        e.type == "fanout.child.dispatch_lost" for e in before)
    assert not any(e.type == "fanout.child.failed" for e in events[len(before):])
    runtime.run_once(events=[])
    assert len(transport.sent) == sent + 1


@pytest.mark.parametrize("fault", ["owner", "request", "attempt", "decision", "decision_action", "request_bytes"])
def test_pending_checkpoint_requires_exact_native_authority(tmp_path, fault):
    from zf.runtime.reader_checkpoint_dispatch import pending_reader_restart

    state, log, _, _, _, op_id = checkpoint(tmp_path)
    events = log.read_all()
    op = reduce_workflow_operations(events)[op_id]
    dispatch = next(e for e in reversed(events) if e.type == "fanout.child.dispatched")
    assert pending_reader_restart(state, events, op, dispatch) is not None
    index = next(i for i,e in enumerate(events) if e.id == op["last_event_id"])
    redrive = events[index]
    if fault in {"owner", "request", "attempt", "decision"}:
        key = {"owner":"recovery_decision_owner", "request":"request_hash",
               "attempt":"source_attempt_id", "decision":"recovery_decision_event_id"}[fault]
        events[index] = replace(redrive, payload={**redrive.payload,key:"foreign"})
    elif fault == "decision_action":
        index = next(i for i,e in enumerate(events) if e.id == redrive.payload["recovery_decision_event_id"])
        events[index] = replace(events[index], payload={**events[index].payload,"safe_resume_action":"trigger_rework"})
    else:
        (state/op["request_ref"]["ref"]).write_text("{}")
    assert pending_reader_restart(state, events, op, dispatch) is None
