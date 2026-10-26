"""A delivered protocol correction must outlive the source provider turn."""
import pytest

from zf.core.events.model import ZfEvent
from zf.core.config.schema import RoleConfig
from zf.runtime.call_result_admission import CallResultAdmissionOutcome, dispatch_call_result_correction
from zf.runtime.call_result_correction import wait_for_source_turn_stop
from tests.test_call_result_runtime import _runtime
from tests.test_reader_fanout_runtime import _start_fanout, _state, _task_map_synth_orchestrator


def closed(role, turn):
    return ZfEvent(type="provider.turn.closed", actor=role, payload={
        "backend": "codex", "turn_id": turn, "provider_status": "completed",
    })


@pytest.mark.parametrize("synth", [False, True])
def test_delivered_correction_fences_old_close_but_new_close_still_recovers(tmp_path, synth):
    if synth:
        _, log, orch, _ = _task_map_synth_orchestrator(tmp_path)
        transport = orch.transport
        role = "task-map-synth"
    else:
        _, log, transport, orch = _state(tmp_path)
        orch.config.workflow.flow_metadata = {"result_protocol": {"mode": "warning"}}
        _start_fanout(orch)
        role = "review-a"
    event_type = "fanout.synth.dispatched" if synth else "fanout.child.dispatched"
    dispatch = next(e for e in reversed(log.read_all()) if e.type == event_type
                    and e.payload["role_instance"] == role)
    old_close = closed(role, "original-turn")
    log.append(old_close)
    log.append(ZfEvent(type="workflow.call.result.repair.dispatched", actor="zf-cli", payload={
        "operation_id": dispatch.payload["operation_id"],
        "request_hash": dispatch.payload["request_hash"],
        "dispatch_id": dispatch.payload["run_id"], "role_instance": role,
    }))
    before = len(transport.sent)
    orch.run_once(events=[])
    assert len(transport.sent) == before
    assert not [e for e in log.read_all() if e.type == "fanout.child.dispatch_lost"]
    # A correction that ends without an admitted result still needs recovery.
    new_close = closed(role, "correction-turn")
    log.append(new_close)
    orch.run_once(events=[new_close])
    assert len(transport.sent) > before


@pytest.mark.parametrize("wrong_key", ["operation_id", "request_hash", "dispatch_id", "role_instance"])
def test_other_correction_cannot_mask_dispatch_loss(tmp_path, wrong_key):
    _, log, transport, orch = _state(tmp_path)
    orch.config.workflow.flow_metadata = {"result_protocol": {"mode": "warning"}}
    _start_fanout(orch)
    dispatch = next(e for e in log.read_all() if e.type == "fanout.child.dispatched"
                    and e.payload["role_instance"] == "review-a")
    old_close = closed("review-a", "original-turn")
    log.append(old_close)
    payload = {"operation_id": dispatch.payload["operation_id"],
               "request_hash": dispatch.payload["request_hash"],
               "dispatch_id": dispatch.payload["run_id"], "role_instance": "review-a"}
    payload[wrong_key] = "other"
    log.append(ZfEvent(type="workflow.call.result.repair.dispatched", actor="zf-cli", payload=payload))
    before = len(transport.sent)
    orch.run_once(events=[old_close])
    assert len(transport.sent) > before


@pytest.mark.parametrize("send_fails", [False, True])
def test_correction_receipt_only_after_successful_transport_send(tmp_path, send_fails):
    runtime = _runtime(tmp_path)
    runtime.config.roles = [RoleConfig(name="reader", backend="mock", role_kind="reader")]
    source = ZfEvent(type="review.child.completed", actor="reader", task_id="T1", payload={
        "workflow_run_id": "workflow-1", "dispatch_id": "dispatch-1",
    })
    runtime._dispatch_context = lambda **kwargs: None

    def send(*args):
        assert not runtime.event_log.read_all()
        if send_fails:
            raise RuntimeError("transport unavailable")

    runtime._send_transport_task = send
    outcome = CallResultAdmissionOutcome(status="repair_pending", mode="blocking",
        operation_id="op-1", request_hash="hash-1", repair_round=1,
        correction_ref={"ref": "artifacts/correction.json"}, correction_dispatch_required=True)
    if send_fails:
        with pytest.raises(RuntimeError, match="transport unavailable"):
            dispatch_call_result_correction(runtime, source_event=source, outcome=outcome)
        assert not runtime.event_log.read_all()
        return
    assert dispatch_call_result_correction(runtime, source_event=source, outcome=outcome)
    [receipt] = runtime.event_log.read_all()
    assert receipt.type == "workflow.call.result.repair.dispatched"
    assert receipt.causation_id == source.id
    assert {key: receipt.payload[key] for key in (
        "operation_id", "request_hash", "dispatch_id", "role_instance",
    )} == {"operation_id": "op-1", "request_hash": "hash-1",
           "dispatch_id": "dispatch-1", "role_instance": "reader"}


@pytest.mark.parametrize("actor,turn_id,expected", [
    ("reader", "turn-1", True), ("other", "turn-1", False), ("reader", "", False),
])
def test_correction_accepts_matching_durable_turn_close(tmp_path, actor, turn_id, expected):
    runtime = _runtime(tmp_path)
    source = ZfEvent(type="review.child.completed", actor="reader")
    runtime.event_log.append(source)
    runtime.event_log.append(closed(actor, turn_id))
    assert wait_for_source_turn_stop(runtime, source_event=source, actor="reader",
                                    backend="codex", timeout_seconds=0) is expected


def test_correction_wait_does_not_reuse_stop_before_source_on_second_poll(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    runtime.event_log.append(ZfEvent(type="codex.hook.stop", actor="reader"))
    source = ZfEvent(type="review.child.completed", actor="reader")
    runtime.event_log.append(source)
    clock = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr("zf.runtime.call_result_correction.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("zf.runtime.call_result_correction.time.sleep", lambda _: None)
    assert not wait_for_source_turn_stop(runtime, source_event=source, actor="reader",
                                         backend="codex", timeout_seconds=1)


def test_correction_timeout_does_not_claim_or_send_delivery(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    runtime.config.roles = [RoleConfig(name="reader", backend="codex", role_kind="reader")]
    source = ZfEvent(type="review.child.completed", actor="reader")
    monkeypatch.setattr("zf.runtime.call_result_correction.wait_for_source_turn_stop",
                        lambda *args, **kwargs: False)
    outcome = CallResultAdmissionOutcome(status="repair_pending", mode="blocking",
        operation_id="op-1", request_hash="hash-1", repair_round=1,
        correction_ref={"ref": "artifacts/correction.json"})
    # No transport method is supplied: timing out must return before sending.
    assert not dispatch_call_result_correction(runtime, source_event=source, outcome=outcome)
    assert not runtime.event_log.read_all()
