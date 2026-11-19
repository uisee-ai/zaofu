"""Controlled Reader retry may deliver a failed never-sent request for the first time."""
from copy import deepcopy
from dataclasses import replace

import pytest
from zf.core.events import ZfEvent
from zf.runtime.fanout import FanoutChild
from zf.runtime.workflow_operation import load_workflow_operation
from tests.test_candidate_reader_retry_authority import _prepared_candidate


def _waiting(tmp_path, *, suspended=True):
    runtime, role, context, original = _prepared_candidate(tmp_path)
    operation_id = original["operation_id"]
    payload = deepcopy(original)
    for key in ("operation_id", "request_hash", "attempt_id", "result_scratch_ref"):
        payload.pop(key, None)
    context = replace(context, expected_children=[FanoutChild(child_id="verify",
        role_instance=role.instance_id, target_ref=context.target_ref, payload=payload)])
    runtime.event_writer.append(context.started_event())
    runtime.event_writer.emit("run.started", payload={"workflow_run_id": context.trace_id})
    if suspended:
        runtime.event_writer.emit("workflow.operation.interrupted", payload={"operation_id": operation_id})
    failed = runtime.event_writer.append(ZfEvent(type="fanout.child.failed", actor="zf-cli", origin="kernel",
        payload={"fanout_id": context.fanout_id, "stage_id": context.stage_id,
                 "child_id": "verify", "role_instance": role.instance_id,
                 "workflow_run_id": context.trace_id, "run_id": original["attempt_id"],
                 "target_ref": context.target_ref, "reason": "environment wait exhausted",
                 "failure_class": "reader_environment_wait_exhausted"}))
    return runtime, context, operation_id, failed


@pytest.mark.parametrize("suspended", [False, True])
def test_failed_wait_reconciles_never_delivered_operation(tmp_path, suspended):
    from zf.runtime.reader_terminal_recovery import reconcile_exhausted_reader_operations
    runtime, _, operation, failed = _waiting(tmp_path, suspended=suspended)
    assert reconcile_exhausted_reader_operations(runtime, runtime.event_log.read_all())
    assert load_workflow_operation(runtime.event_log, operation)["status"] == "failed"
    assert not reconcile_exhausted_reader_operations(runtime, runtime.event_log.read_all())
    assert not runtime.transport.sent


@pytest.mark.parametrize("settled_first", [False, True])
def test_controlled_first_delivery_preserves_history_and_fresh_identity(tmp_path, settled_first):
    runtime, context, old, failed = _waiting(tmp_path)
    if settled_first:
        from zf.runtime.reader_terminal_recovery import reconcile_exhausted_reader_operations
        assert reconcile_exhausted_reader_operations(runtime, runtime.event_log.read_all())
    result = runtime._rerun_fanout_child(context.fanout_id, "verify")
    assert result.action == "rerun_fanout_child", result.reason
    events = runtime.event_log.read_all()
    dispatched = [e for e in events if e.type == "fanout.child.dispatched"]
    assert len(dispatched) == 1
    current = dispatched[0]
    assert current.causation_id == failed.id
    assert current.payload["operation_id"] != old
    assert current.payload["payload"]["retry_of_operation_id"] == old
    assert load_workflow_operation(runtime.event_log, old)["status"] == "failed"
    assert len(runtime.transport.sent) == 1


@pytest.mark.parametrize("fault", ["started", "paused", "cancelled", "dispatch_paused", "foreign", "stale", "foreign_failure", "late_suspend"])
def test_first_delivery_repair_fails_closed(tmp_path, monkeypatch, fault):
    runtime, context, operation, _ = _waiting(tmp_path)
    if fault == "started":
        runtime.event_writer.emit("workflow.operation.started", payload={"operation_id": operation})
    elif fault in {"paused", "cancelled"}:
        runtime.event_writer.emit("run." + fault, payload={"workflow_run_id": context.trace_id})
    elif fault == "dispatch_paused":
        runtime.event_writer.emit("dispatch.paused")
    elif fault == "foreign":
        runtime.event_writer.emit("workflow.operation.cancelled", payload={"operation_id": operation})
    elif fault == "foreign_failure":
        runtime.event_writer.emit("workflow.operation.failed", payload={"operation_id": operation})
    elif fault == "late_suspend":
        runtime.event_writer.emit("workflow.operation.interrupted", payload={"operation_id": operation})
    else:
        monkeypatch.setattr(runtime, "_fanout_identity_stale_reason", lambda _: ("superseded", "new"))
    before = load_workflow_operation(runtime.event_log, operation)
    result = runtime._rerun_fanout_child(context.fanout_id, "verify")
    assert result.action == "rerun_fanout_child_failed"
    assert not runtime.transport.sent
    assert load_workflow_operation(runtime.event_log, operation) == before


def test_structured_repair_entry_first_delivery_is_idempotent(tmp_path):
    runtime, context, old, _ = _waiting(tmp_path)
    intent = runtime.event_writer.append(ZfEvent(type="repair.action.requested", actor="operator",
        payload={"action_id": "repair", "idempotency_key": "repair", "kind": "rerun_fanout_child",
                 "fanout_id": context.fanout_id, "fanout_child_id": "verify"}))
    runtime.run_once(events=[intent])
    runtime.run_once(events=[intent])
    assert len(runtime.transport.sent) == 1
    assert len([e for e in runtime.event_log.read_all() if e.type == "repair.action.applied"
                and e.payload.get("action_id") == "repair"]) == 1
    assert load_workflow_operation(runtime.event_log, old)["status"] == "failed"


def test_next_retry_after_first_delivery_uses_a_distinct_attempt(tmp_path):
    from zf.runtime.call_result_runtime import workflow_operation_service
    runtime, context, _, _ = _waiting(tmp_path)
    assert runtime._rerun_fanout_child(context.fanout_id, "verify").action == "rerun_fanout_child"
    first = next(e for e in runtime.event_log.read_all() if e.type == "fanout.child.dispatched")
    op = load_workflow_operation(runtime.event_log, first.payload["operation_id"])
    failed = runtime.event_writer.append(ZfEvent(type="fanout.child.failed", actor="zf-cli",
        payload={**first.payload, "reason": "test provider fault"}))
    workflow_operation_service(runtime).fail(operation_id=op["operation_id"],
        request_hash=op["request_hash"], workflow_run_id=context.trace_id,
        reason="test provider fault", causation_id=failed.id)
    result = runtime._rerun_fanout_child(context.fanout_id, "verify")
    assert result.action == "rerun_fanout_child", result.reason
    last = [e for e in runtime.event_log.read_all() if e.type == "fanout.child.dispatched"][-1]
    assert last.payload["run_id"].endswith("-retry-2")
    assert last.payload["operation_id"] != first.payload["operation_id"]
