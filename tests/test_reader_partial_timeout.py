"""One idle Reader must not discard healthy siblings in a wait-for-all batch."""
from dataclasses import replace
import time

import pytest

from zf.core.events import ZfEvent
from tests.test_fanout_hardening import _state, _start, _fanout_id


@pytest.mark.parametrize("mode", ["wait_for_all", "any_failed_fail"])
def test_partial_reader_idle_failure_respects_aggregate_policy(tmp_path, mode):
    _, log, _, runtime = _state(tmp_path, idle_threshold=10)
    runtime.config.roles.append(replace(runtime.config.roles[0], name="review-b",
        instance_id="review-b", stuck_threshold_seconds=10000))
    stage = runtime.config.workflow.stages[0]
    stage.roles.append("review-b")
    stage.aggregate.mode = mode
    _start(runtime)
    runtime._now = lambda: time.time() + 100
    runtime._check_fanout_timeouts()
    failures = [e for e in log.read_all() if e.type == "fanout.child.failed"]
    assert [e.payload["child_id"] for e in failures] == ["review-a"]
    terminals = [e for e in log.read_all() if e.type == "review.rejected"]
    if mode == "any_failed_fail":
        assert terminals
        return
    assert not terminals
    assert not any(e.type == "fanout.timed_out" for e in log.read_all())
    fanout = _fanout_id(log)
    runtime.run_once(events=[ZfEvent(type="workflow.child.completed", actor="review-b",
        correlation_id="trace-1", payload={"fanout_id": fanout, "child_id": "review-b",
        "run_id": f"run-{fanout}-review-b", "status": "completed"})])
    assert any(e.type == "fanout.child.completed" and e.payload.get("child_id") == "review-b"
               for e in log.read_all())
    assert len([e for e in log.read_all() if e.type == "review.rejected"]) == 1


def test_timeout_failure_preserves_parent_identity_and_explains_idle_deadline(tmp_path):
    _, log, _, runtime = _state(tmp_path, idle_threshold=10)
    _start(runtime)
    runtime._now = lambda: time.time() + 100
    runtime._check_fanout_timeouts()
    failure = next(e for e in log.read_all() if e.type == "review.rejected")
    assert failure.payload["workflow_run_id"] == "trace-1"
    assert failure.payload["pdd_id"] == "F-11111111"
    assert failure.payload["failure_kind"] == "infra"
    assert "0s" not in failure.payload["findings"][0]["message"]


@pytest.mark.parametrize("fence", ["", "paused", "cancelled", "dispatch_paused", "stale", "wrong_hash", "wrong_attempt", "stage_deadline"])
def test_recovery_entry_resumes_only_healthy_calls_after_legacy_partial_timeout(tmp_path, monkeypatch, fence):
    from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations

    _, log, _, runtime = _state(tmp_path, idle_threshold=10)
    runtime.config.roles.append(replace(runtime.config.roles[0], name="review-b",
        instance_id="review-b", stuck_threshold_seconds=10000))
    runtime.config.workflow.stages[0].roles.append("review-b")
    _start(runtime)
    runtime.event_writer.append(ZfEvent(type="run.started", origin="kernel",
        payload={"workflow_run_id": "trace-1"}))
    service = WorkflowOperationService(state_dir=runtime.state_dir, event_log=log,
                                      event_writer=runtime.event_writer)
    for dispatch in [e for e in log.read_all() if e.type == "fanout.child.dispatched"]:
        role = dispatch.payload["role_instance"]
        service.ensure_operation(workflow_run_id="trace-1", operation_id=role,
            operation_type="fanout_reader_child", role_instance=role,
            parent_stage_id="review-candidate", active_attempt_id=role, request={})
        operation = reduce_workflow_operations(log.read_all())[role]
        runtime.event_writer.emit("workflow.operation.started", payload={"operation_id": role})
        binding = {"operation_id": role, "request_hash": operation["request_hash"], "attempt_id": role}
        if role == "review-b" and fence in {"wrong_hash", "wrong_attempt"}:
            binding["request_hash" if fence == "wrong_hash" else "attempt_id"] = "foreign"
        runtime.event_writer.append(ZfEvent(type="fanout.child.dispatched", origin="kernel",
            payload={**dispatch.payload, **binding, "payload": binding}))
    with monkeypatch.context() as legacy:
        legacy.setattr(runtime, "_defer_partial_reader_timeout", lambda **_: False)
        runtime._now = lambda: time.time() + 100
        runtime._check_fanout_timeouts()
    fanout = _fanout_id(log)
    failed = next(e for e in log.read_all() if e.type == "fanout.aggregate.completed")
    before = reduce_workflow_operations(log.read_all())
    if fence in {"paused", "cancelled"}:
        runtime.event_writer.append(ZfEvent(type="run." + fence, origin="kernel",
            payload={"workflow_run_id": "trace-1"}))
    elif fence == "dispatch_paused":
        runtime.event_writer.append(ZfEvent(type="dispatch.paused", origin="kernel"))
    elif fence == "stale":
        monkeypatch.setattr(runtime, "_fanout_identity_stale_reason", lambda _: ("superseded", "new"))
    elif fence == "stage_deadline":
        # A later whole-stage deadline must never be reinterpreted as partial idle.
        runtime.event_writer.append(ZfEvent(type="fanout.aggregate.completed", actor="zf-cli",
            payload={**failed.payload, "timeout_seconds": 10}, causation_id=failed.causation_id))
    runtime._recover_unrecorded_reader_fanout_results()
    repairs = [e for e in log.read_all() if e.type == "fanout.aggregate.started"
               and e.payload.get("recovery_kind") == "partial_reader_idle_timeout"]
    if fence:
        assert not repairs
    else:
        assert len(repairs) == 1
        manifest = runtime._fanout_manifest(fanout)
        assert manifest["status"] == "running"
        assert manifest["aggregate"]["status"] == "pending"
        assert manifest["aggregate"]["recovered_from_event_id"] == failed.id
        assert [c["status"] for c in manifest["children"]] == ["failed", "dispatched"]
        assert reduce_workflow_operations(log.read_all()) == before
        runtime._recover_unrecorded_reader_fanout_results()
        assert len([e for e in log.read_all() if e.payload.get("recovery_kind") == "partial_reader_idle_timeout"]) == 1
