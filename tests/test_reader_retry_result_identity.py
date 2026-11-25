"""A replacement Reader consumes only evidence for its own actual dispatch."""

from dataclasses import replace

import pytest

from tests.test_reader_work_record import candidate_reader
from zf.runtime.result_submit import SemanticResultSubmitService
from zf.runtime.workflow_operation import reduce_workflow_operations


@pytest.mark.parametrize("resume", [False, True])
def test_old_admitted_candidate_failure_cannot_settle_a_new_worker(tmp_path, resume):
    state, log, runtime, old, _, token = candidate_reader(tmp_path)
    service = SemanticResultSubmitService(
        state_dir=state, event_log=log, event_writer=runtime.event_writer
    )

    def submit(op):
        result = service.submit(
            operation_id=op["operation_id"],
            role_instance="verify",
            credential=token,
            semantic_result={
                "schema_version": "verification-result.v1",
                "execution_status": "failed",
                "verdict": "abstained",
                "failure_class": "reader_execution_failure",
                "summary": "Isolated runner stopped before the assertion.",
                "evidence_refs": [op["request_ref"]["ref"]],
                "verification_owner": "candidate_verify",
                "verification_tier": "runtime",
                "requirement_results": [],
                "probe_receipts": [],
                "reused_command_receipt_ids": [],
                "rework_items": [],
            },
        )
        return next(e for e in log.read_all() if e.id == result.canonical_event_id)

    previous_result = submit(old)
    runtime._maybe_update_reader_fanout(previous_result)
    fanout = previous_result.payload["fanout_id"]
    role = previous_result.payload["child_id"]
    action = runtime._rerun_fanout_child(fanout, role)
    assert action.action == "rerun_fanout_child", action
    current = next(
        op
        for op in reduce_workflow_operations(log.read_all()).values()
        if op["status"] == "running"
    )
    before = len(log.read_all())
    runtime._maybe_update_reader_fanout(previous_result)
    assert len(log.read_all()) == before
    assert (
        runtime._fanout_child(runtime._fanout_manifest(fanout), role)["status"]
        == "dispatched"
    )

    fresh = submit(current)
    # A matching operation cannot hide conflicting attempt/digest fields.
    for key in ("operation_id", "attempt_id", "request_hash"):
        mixed = replace(
            fresh, payload={**fresh.payload, key: previous_result.payload[key]}
        )
        runtime._maybe_update_reader_fanout(mixed)
        assert (
            runtime._fanout_child(runtime._fanout_manifest(fanout), role)["status"]
            == "dispatched"
        )
    if resume:
        from zf.runtime.orchestrator import Orchestrator

        restarted = Orchestrator(
            state, runtime.config, runtime.transport, project_root=runtime.project_root
        )
        assert restarted._resume_unrecorded_reader_fanout_results(log.read_all())
    else:
        runtime._maybe_update_reader_fanout(fresh)
    runtime._maybe_update_reader_fanout(fresh)
    settled = [
        e
        for e in log.read_all()[before:]
        if e.type == "fanout.child.failed" and e.payload.get("child_id") == role
    ]
    assert [e.causation_id for e in settled] == [fresh.id]


@pytest.mark.parametrize(
    "field",
    ["operation_id", "request_hash", "attempt_id", "active_attempt_id", "run_id"],
)
def test_typed_kernel_failure_can_use_its_current_dispatch_locator(field):
    from zf.runtime.fanout_retry_projection import reader_result_dispatch_id

    current = {
        "operation_id": "new",
        "request_hash": "digest",
        "attempt_id": "dispatch",
    }
    child = {
        "run_id": "dispatch",
        "payload": {
            **current,
            "retry_of_operation_id": "old",
            "result_protocol_mode": "blocking",
        },
    }
    value = current.get(field, "dispatch")
    assert reader_result_dispatch_id(child, {field: value}) == "dispatch"
    assert reader_result_dispatch_id(child, {field: "old"}) is None
    assert reader_result_dispatch_id(child, {}) is None
    assert reader_result_dispatch_id({"payload": {}}, {}) == ""
    assert (
        reader_result_dispatch_id(child, {"operation_id": "new", "run_id": "None"})
        == "dispatch"
    )


def test_legacy_non_blocking_retry_can_still_receive_an_unbound_report():
    from zf.runtime.fanout_retry_projection import reader_result_dispatch_id

    child = {
        "run_id": "new-dispatch",
        "payload": {"operation_id": "new", "retry_of_operation_id": "old"},
    }
    assert reader_result_dispatch_id({"run_id": "initial", "payload": []}, {}) == "initial"
    assert reader_result_dispatch_id(child, {}) == "new-dispatch"
    assert reader_result_dispatch_id(child, {"operation_id": "old"}) is None
