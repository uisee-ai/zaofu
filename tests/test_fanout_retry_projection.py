"""Replay cannot reopen terminal reader aggregates from unrelated dispatches."""
import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.fanout import FanoutChild, FanoutContext, FanoutManifestProjector


@pytest.mark.parametrize("fault", [
    "same_operation", "wrong_parent", "wrong_run", "writer",
    "completed_aggregate", "cancelled_aggregate", "completed_child", "missing_run",
])
def test_unbound_dispatch_does_not_reopen_aggregate(tmp_path, fault):
    child = FanoutChild(child_id="reader", role_instance="reader", target_ref="candidate")
    context = FanoutContext(
        fanout_id="f", stage_id="verify", topology="fanout_reader",
        trace_id="run", trigger_event_id="trigger", target_ref="candidate",
        expected_children=[child],
    )
    started = context.started_event()
    if fault == "writer": started.payload["topology"] = "fanout_writer_scoped"
    first = context.child_dispatched_event(child, run_id="old-dispatch")
    first.payload["payload"] = {"operation_id": "old-operation"}
    child_terminal = ZfEvent(type="fanout.child.failed", payload={
        "fanout_id": "f", "child_id": "reader", "run_id": "old-dispatch",
    })
    if fault == "completed_child": child_terminal.type = "fanout.child.completed"
    status = {"completed_aggregate": "completed", "cancelled_aggregate": "cancelled"}.get(fault, "failed")
    aggregate = ZfEvent(type="fanout.aggregate.completed", payload={"fanout_id": "f", "status": status})
    retry = context.child_dispatched_event(child, run_id="new-dispatch")
    retry.payload.update(retry_of_run_id="old-dispatch", payload={
        "operation_id": "new-operation", "retry_of_operation_id": "old-operation",
    })
    if fault == "same_operation": retry.payload["payload"]["operation_id"] = "old-operation"
    if fault == "wrong_parent": retry.payload["payload"]["retry_of_operation_id"] = "other-operation"
    if fault == "wrong_run": retry.payload["retry_of_run_id"] = "other-dispatch"
    if fault == "missing_run": retry.payload.pop("run_id")
    manifest = FanoutManifestProjector(tmp_path).rebuild("f", [started, first, child_terminal, aggregate, retry])
    assert manifest["aggregate"]["status"] == status
    assert "recovered_from_event_id" not in manifest["aggregate"]
