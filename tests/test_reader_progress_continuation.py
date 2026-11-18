"""Reader progress is continued without consuming infrastructure retries."""
from __future__ import annotations

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations
from tests.test_reader_fanout_runtime import (
    _durable_reader_state, _durable_verification_payload, _manifest,
)


def read_page(state, child, start):
    manifest = hydrate_sidecar_ref(state, child["payload"]["attempt_source_manifest"]).payload
    return read_attempt_artifact(state, manifest=manifest, source_id="context",
                                 artifact_id="context", json_path="$.facts",
                                 page_start=start, page_size=1)


def close_turn(runtime, number):
    event = runtime.event_writer.append(ZfEvent(
        type="provider.turn.closed", actor="verify-1",
        payload={"backend": "codex", "turn_id": f"turn-{number}",
                 "provider_status": "completed"},
    ))
    runtime.run_once(events=[event])
    return event


def test_reader_continues_multiple_new_pages_then_admits_result(tmp_path):
    state, log, transport, runtime, _, child = _durable_reader_state(tmp_path)
    operation_id = child["payload"]["operation_id"]
    original = reduce_workflow_operations(log.read_all())[operation_id]
    for offset in range(4):
        read_page(state, child, offset)
        close_turn(runtime, offset)
        current = reduce_workflow_operations(log.read_all())[operation_id]
        assert current["status"] == "running"
        assert current["active_attempt_id"] == original["active_attempt_id"]
        assert current["request_hash"] == original["request_hash"]
    assert len(transport.sent) == 5
    assert not [e for e in log.read_all() if e.type == "fanout.child.dispatch_lost"]
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
    assert reduce_workflow_operations(log.read_all())[operation_id]["status"] == "settled"


def test_duplicate_pages_cannot_fund_unlimited_reader_continuations(tmp_path):
    state, log, _, runtime, _, child = _durable_reader_state(tmp_path)
    for offset in range(4):
        read_page(state, child, 0)
        close_turn(runtime, offset)
    failures = [e for e in log.read_all() if e.type == "fanout.child.failed"]
    assert failures
    assert "recovery_exhausted" in failures[-1].payload["reason"]


def test_claim_survives_crash_without_blind_resend(tmp_path, monkeypatch):
    from zf.runtime.orchestrator import Orchestrator

    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path)
    read_page(state, child, 0)
    def interrupted(**kwargs):
        raise OSError("unknown transport delivery")
    monkeypatch.setattr(runtime, "_retry_fanout_child", interrupted)
    close_turn(runtime, 1)
    claims = [e for e in log.read_all() if e.type == "worker.continuation.claimed"]
    assert len(claims) == 1
    restarted = Orchestrator(state, config, transport)
    restarted.run_once(events=[])
    assert len(transport.sent) == 1
    assert not [e for e in log.read_all() if e.type == "fanout.child.failed"]


def test_replayed_closed_turn_does_not_continue_twice(tmp_path):
    state, log, transport, runtime, _, child = _durable_reader_state(tmp_path)
    read_page(state, child, 0)
    event = close_turn(runtime, 1)
    for _ in range(3):
        runtime.run_once(events=[event])
    assert len(transport.sent) == 2
    assert len([e for e in log.read_all() if e.type == "worker.continuation.claimed"]) == 1


def test_crash_after_dispatch_reconciles_delivery_then_accepts_new_progress(tmp_path, monkeypatch):
    from zf.runtime.orchestrator import Orchestrator

    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path)
    read_page(state, child, 0)
    append = runtime.event_writer.append
    def interrupted(event):
        if event.type == "worker.continuation.reconciled":
            raise OSError("crash after delivery")
        return append(event)
    monkeypatch.setattr(runtime.event_writer, "append", interrupted)
    close_turn(runtime, 1)
    assert len(transport.sent) == 2
    restarted = Orchestrator(state, config, transport)
    read_page(state, child, 1)
    close_turn(restarted, 2)
    assert len(transport.sent) == 3
    receipts = [e for e in log.read_all() if e.type == "worker.continuation.reconciled"]
    assert len(receipts) == 2
    assert receipts[0].payload.get("delivery_event_id")
    assert all(e.payload["business_completed"] is False for e in receipts)


@pytest.mark.parametrize("wait", ["pause", "pending_tool", "assistance", "approval"])
def test_progress_cannot_override_current_wait(tmp_path, wait):
    state, log, transport, runtime, _, child = _durable_reader_state(tmp_path)
    read_page(state, child, 0)
    payload = child["payload"]
    if wait == "pause":
        runtime.event_writer.append(ZfEvent(type="dispatch.paused", actor="operator"))
    elif wait == "pending_tool":
        runtime.event_writer.append(ZfEvent(type="agent.tool.use", actor="verify-1",
                                            payload={"tool_use_id": "unfinished"}))
    elif wait == "approval":
        runtime.event_writer.append(ZfEvent(
            type="approval.requested", actor="verify-1", task_id="T-VERIFY",
            payload={"approval_ref": "waiting-approval"},
        ))
    else:
        runtime.event_writer.append(ZfEvent(
            type="worker.assistance.requested", actor="verify-1", task_id="T-VERIFY",
            payload={"request_id": "waiting", "operation_id": payload["operation_id"],
                     "active_attempt_id": payload["attempt_id"]},
        ))
    before = len(transport.sent)
    close_turn(runtime, 1)
    assert len(transport.sent) == before


def test_failed_turn_with_new_reads_still_uses_infrastructure_recovery(tmp_path):
    state, log, _, runtime, _, child = _durable_reader_state(tmp_path)
    read_page(state, child, 0)
    event = runtime.event_writer.append(ZfEvent(
        type="provider.turn.closed", actor="verify-1",
        payload={"backend": "codex", "turn_id": "failed", "provider_status": "failed"},
    ))
    runtime.run_once(events=[event])
    assert not any(e.type == "worker.continuation.claimed" for e in log.read_all())
    assert any(e.type == "fanout.child.dispatch_lost" for e in log.read_all())


def test_failed_turn_does_not_replay_unknown_tool_effect(tmp_path):
    state, log, transport, runtime, _, child = _durable_reader_state(tmp_path)
    read_page(state, child, 0)
    event = runtime.event_writer.append(ZfEvent(type="provider.turn.closed", actor="verify-1",
        payload={"provider_status": "failed", "pending_tool_ids": ["unknown-effect"]}))
    runtime.run_once(events=[event])
    assert len(transport.sent) == 1
    assert not any(e.type == "fanout.child.dispatch_lost" for e in log.read_all())
