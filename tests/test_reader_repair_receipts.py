"""Unknown Reader correction delivery reconciles without another paid turn."""
import hashlib

import pytest

from tests.test_result_repair_delivery import rejected_reader
from zf.core.events import ZfEvent
from zf.runtime.call_result_repair_reconciliation import reconcile_call_result_repairs
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_attempt_runtime import task_attempt_store
from zf.runtime.workflow_operation import reduce_workflow_operations


@pytest.mark.parametrize("receipt_kind", ["native", "stream"])
@pytest.mark.parametrize("paused", [False, True])
def test_reader_lost_send_reconciles_via_periodic_sweep(tmp_path, receipt_kind, paused):
    state, log, transport, runtime, config, child = rejected_reader(tmp_path)
    sends = []

    def accepted_then_lost(role, path, prompt, **kwargs):
        sends.append(prompt)
        prepared = next(e for e in reversed(log.read_all()) if e.type == "worker.continuation.prepared")
        if receipt_kind == "native":
            event_type = "codex.hook.user_prompt_submit"
            payload = {"session_id": "session-1", "turn_id": "repair-turn",
                       "submitted_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
        else:
            event_type = "provider.turn.opened"
            payload = prepared.payload.copy()
        runtime.event_writer.append(ZfEvent(type=event_type, origin="kernel", actor=role, payload=payload))
        raise OSError("delivery return lost")

    transport.send_task = accepted_then_lost
    before = task_attempt_store(runtime).current_rows()
    with pytest.raises(OSError, match="delivery return lost"):
        reconcile_call_result_repairs(runtime)
    operation_id = child["payload"]["operation_id"]
    operation = reduce_workflow_operations(log.read_all())[operation_id]
    assert operation["active_attempt_id"] != before[0]["attempt_id"]
    assert runtime.task_store.get(operation["task_id"]) is None
    if paused:
        runtime.event_writer.emit("dispatch.paused")
    restarted = Orchestrator(state, config, transport)
    restarted.run_once(events=[])
    restarted.run_once(events=[])
    receipts = [e for e in log.read_all() if e.type == "worker.continuation.reconciled"]
    assert len(receipts) == 1
    assert receipts[0].payload["delivery_status"] == "observed"
    assert receipts[0].payload["recovery_action"] == "result_protocol_correction"
    assert receipts[0].payload["business_completed"] is False
    assert len(sends) == 1
    assert task_attempt_store(restarted).current_rows() == before
    assert reduce_workflow_operations(log.read_all())[operation_id]["status"] == "running"
    dispatched = [e for e in log.read_all() if e.type == "workflow.call.result.repair.dispatched"]
    assert len(dispatched) == (0 if paused else 1)
    if dispatched:
        assert dispatched[0].payload["receipt_event_id"] == receipts[0].id


@pytest.mark.parametrize("fault", ["no_receipt", "wrong_actor", "wrong_prompt", "worker_origin",
                                  "old_dispatch", "old_attempt", "expired", "new_operation"])
def test_reader_unknown_send_cannot_be_resolved_by_unbound_evidence(tmp_path, fault):
    from zf.runtime.continuation_delivery import reconcile_continuation_deliveries
    state, log, transport, runtime, config, child = rejected_reader(tmp_path)
    sends = []
    def lost(role, path, prompt, **kwargs):
        sends.append(prompt)
        raise OSError("lost")
    transport.send_task = lost
    with pytest.raises(OSError):
        reconcile_call_result_repairs(runtime)
    prepared = next(e for e in reversed(log.read_all()) if e.type == "worker.continuation.prepared")
    payload = prepared.payload.copy()
    if fault in {"old_dispatch", "old_attempt"}:
        payload["dispatch_id" if fault == "old_dispatch" else "attempt_id"] = "old"
    elif fault == "expired":
        row = task_attempt_store(runtime).current_rows()[0]
        task_attempt_store(runtime).renew_lease(row["attempt_id"], updated_at=row["updated_at"],
            lease_expires_at="2000-01-01T00:00:00+00:00")
    elif fault == "new_operation":
        runtime.event_writer.emit("workflow.operation.started", payload={
            "operation_id": payload["operation_id"], "dispatch_id": "replacement",
        })
    if fault != "no_receipt":
        if fault in {"wrong_actor", "wrong_prompt", "worker_origin"}:
            runtime.event_writer.append(ZfEvent(type="codex.hook.user_prompt_submit",
                origin="worker" if fault == "worker_origin" else "kernel",
                actor="other" if fault == "wrong_actor" else prepared.actor,
                payload={"session_id": "s", "turn_id": "t",
                         "submitted_prompt_sha256": "different" if fault == "wrong_prompt"
                         else prepared.payload["prompt_sha256"]}))
        else:
            runtime.event_writer.append(ZfEvent(type="provider.turn.opened", origin="kernel",
                                               actor=prepared.actor, payload=payload))
    assert reconcile_continuation_deliveries(runtime) == 0
    assert not any(e.type == "worker.continuation.reconciled" for e in log.read_all())
    assert len(sends) == 1
