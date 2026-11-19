"""Exercise TaskAttempt enforce through the real reader continuation entrypoint."""
import pytest

from zf.core.events import ZfEvent
from zf.runtime.task_attempt_runtime import task_attempt_store
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.workflow_operation import reduce_workflow_operations
from tests.test_reader_fanout_runtime import (
    _durable_reader_state, _durable_verification_payload, _manifest,
)
from tests.test_reader_progress_continuation import read_page, close_turn


def test_reader_progress_preserves_enforced_attempt_across_four_turns(tmp_path):
    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path)
    config.workflow.task_attempt.mode = "enforce"
    config.workflow.task_attempt.max_attempts = 3
    store = task_attempt_store(runtime)
    original = store.current_rows()
    assert len(original) == 1 and original[0]["status"] == "sent"
    for offset in range(4):
        read_page(state, child, offset)
        close_turn(runtime, offset)
    assert len(transport.sent) == 5
    assert store.current_rows() == original
    assert not [e for e in log.read_all() if e.type in {"task.attempt.superseded", "task.attempt.deadlettered", "fanout.child.failed"}]
    for _, _, _, context in transport.sent[1:]:
        assert context.continuation and context.continuation_id
        assert context.attempt_id == original[0]["attempt_id"]
        assert context.lease_id == original[0]["lease_id"]
        assert context.dispatch_id == original[0]["dispatch_id"]
    manifest = hydrate_sidecar_ref(state, child["payload"]["attempt_source_manifest"]).payload
    read_attempt_artifact(state, manifest=manifest, source_id="context",
                          artifact_id="context", json_path="$.facts")
    latest = _manifest(state, child["fanout_id"])["children"][0]
    latest["fanout_id"] = child["fanout_id"]
    result = runtime.event_writer.append(ZfEvent(
        type="verify.child.completed", actor="verify-1", task_id="T-VERIFY",
        correlation_id="run-durable",
        payload=_durable_verification_payload(latest, state_dir=state, verdict="passed"),
    ))
    runtime.run_once(events=[result])
    assert reduce_workflow_operations(log.read_all())[original[0]["operation_id"]]["status"] == "settled"


@pytest.mark.parametrize("fault", ["expired", "settled", "dispatch", "budget", "dead", "pause"])
def test_reader_continuation_keeps_send_time_fences(tmp_path, monkeypatch, fault):
    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path)
    config.workflow.task_attempt.mode = "enforce"
    store = task_attempt_store(runtime)
    row = store.current_rows()[0]
    read_page(state, child, 0)
    original = runtime._retry_fanout_child

    def race(**kwargs):
        if fault == "expired":
            store.renew_lease(row["attempt_id"], lease_expires_at="2000-01-01T00:00:00Z", updated_at="2000-01-01T00:00:00Z")
        elif fault == "settled":
            runtime.event_writer.append(ZfEvent(type="workflow.operation.settled", origin="kernel",
                payload={"operation_id": row["operation_id"], "workflow_run_id": row["run_id"]}))
        elif fault == "dispatch":
            store.update(row["attempt_id"], dispatch_id="stale")
        elif fault == "budget":
            monkeypatch.setattr(runtime, "_budget_exceeded", lambda *_: True)
        elif fault == "dead":
            monkeypatch.setattr(transport, "is_alive", lambda *_: False)
        else:
            runtime.event_writer.append(ZfEvent(type="dispatch.paused", origin="kernel"))
        return original(**kwargs)

    monkeypatch.setattr(runtime, "_retry_fanout_child", race)
    close_turn(runtime, 1)
    assert len(transport.sent) == 1
    assert not any(e.type == "worker.continuation.reconciled"
                   and e.payload.get("delivery_status") == "delivered" for e in log.read_all())
    assert not any(e.type == "task.attempt.superseded" for e in log.read_all())


def test_unknown_reader_send_does_not_claim_delivery_or_resend(tmp_path, monkeypatch):
    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path)
    config.workflow.task_attempt.mode = "enforce"
    before = task_attempt_store(runtime).current_rows()
    read_page(state, child, 0)

    def interrupted(*args, **kwargs):
        raise OSError("transport outcome unknown")

    monkeypatch.setattr(transport, "send_task", interrupted)
    close_turn(runtime, 1)
    runtime.run_once(events=[])
    assert task_attempt_store(runtime).current_rows() == before
    assert len([e for e in log.read_all() if e.type == "worker.continuation.claimed"]) == 1
    assert not any(e.type == "worker.continuation.reconciled"
                   and e.payload.get("delivery_status") == "delivered" for e in log.read_all())
