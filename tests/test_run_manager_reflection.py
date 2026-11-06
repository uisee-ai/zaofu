"""Real tick caller with a blocked fake provider and durable resume windows."""

from datetime import datetime, timedelta, timezone
from threading import Event
from time import monotonic, sleep

import pytest
from tests.test_run_manager import _state, _reflect_config, _write_supervisor_attention
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.run_manager_reflection import (
    COMPLETED, REQUESTED, reconcile_reflections, submit_reflection,
)
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


def _wait(predicate):
    deadline = monotonic() + 3
    while not predicate() and monotonic() < deadline:
        sleep(0.01)
    assert predicate()


def test_slow_reflection_does_not_hold_tick_and_cannot_duplicate_diagnosis(tmp_path):
    state, log, writer = _state(tmp_path)
    _write_supervisor_attention(state)
    entered, release = Event(), Event()
    calls = []

    def reflect(prompt, **kwargs):
        calls.append(prompt)
        entered.set()
        assert release.wait(3)
        return {"verdict": "unknown", "recommended_route": "autoresearch"}

    try:
        start = monotonic()
        first = run_manager_tick(state_dir=state, writer=writer, config=_reflect_config(),
                                 project_root=tmp_path, spawn_repairs=False, reflect_fn=reflect)
        assert monotonic() - start < 1
        assert entered.wait(1)
        assert first.reflect_requested == 1 and first.reflect_completed == 0
        second = run_manager_tick(state_dir=state, writer=writer, config=_reflect_config(),
                                  project_root=tmp_path, spawn_repairs=False, reflect_fn=reflect)
        assert first.autoresearch_requested == second.autoresearch_requested == 0
        assert len(calls) == 1
    finally:
        release.set()
    _wait(lambda: any(e.type == COMPLETED for e in log.read_all()))
    third = run_manager_tick(state_dir=state, writer=writer, config=_reflect_config(),
                             project_root=tmp_path, spawn_repairs=False, reflect_fn=reflect)
    assert third.autoresearch_requested == 1
    assert len(calls) == 1


def test_deadline_settles_once_and_rejects_late_result(tmp_path):
    state, log, writer = _state(tmp_path)
    release, returned = Event(), Event()

    def invoke(prompt):
        release.wait(3)
        returned.set()
        return {"verdict": "best_so_far"}

    try:
        submit_reflection(state_dir=state, writer=writer, request_id="deadline",
                          prompt="diagnose only", metadata={}, timeout_seconds=1, invoke=invoke)
        reconcile_reflections(state_dir=state, writer=writer, invoke=invoke,
                              now=datetime.now(timezone.utc) + timedelta(seconds=2))
        assert [e.payload["status"] for e in log.read_all() if e.type == COMPLETED] == ["timeout"]
    finally:
        release.set()
    assert returned.wait(1)
    _wait(lambda: next(iter(reduce_workflow_operations(log.read_all()).values()))["status"] == "settled")
    assert len([e for e in log.read_all() if e.type == COMPLETED]) == 1


def test_restart_after_durable_request_before_dispatch_resumes_handler(tmp_path):
    state, log, writer = _state(tmp_path)
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=writer)
    service.ensure_operation(workflow_run_id="run", operation_id="wop-recovery",
        operation_type="run_manager_reflect", request={
            "request_id": "recovery", "prompt": "frozen failure evidence",
            "metadata": {}, "timeout_seconds": 30,
        })
    calls = []
    def invoke(prompt):
        calls.append(prompt)
        return {"verdict": "unknown"}
    reconcile_reflections(state_dir=state, writer=writer, invoke=invoke)
    _wait(lambda: any(e.type == COMPLETED for e in log.read_all()))
    reconcile_reflections(state_dir=state, writer=writer, invoke=invoke)
    assert calls == ["frozen failure evidence"]
    assert len([e for e in log.read_all() if e.type == REQUESTED]) == 1
    assert not any(e.type == "task.dispatched" for e in log.read_all())


def test_paused_request_waits_then_resumes_without_model_replanning(tmp_path):
    state, log, writer = _state(tmp_path)
    writer.emit("dispatch.paused", actor="operator")
    calls = []
    def invoke(prompt):
        calls.append(prompt)
        return {"verdict": "best_so_far"}
    submit_reflection(state_dir=state, writer=writer, request_id="paused",
                      prompt="exact saved inputs", metadata={}, timeout_seconds=30, invoke=invoke)
    assert calls == []
    assert not any(e.type == REQUESTED for e in log.read_all())
    writer.emit("dispatch.resumed", actor="operator")
    reconcile_reflections(state_dir=state, writer=writer, invoke=invoke)
    _wait(lambda: any(e.type == COMPLETED for e in log.read_all()))
    assert calls == ["exact saved inputs"]


def test_settled_result_replays_missing_notification_without_provider(tmp_path):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    state, log, writer = _state(tmp_path)
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=writer)
    service.ensure_operation(workflow_run_id="run", operation_id="wop-settled",
        operation_type="run_manager_reflect", request={"request_id": "settled", "prompt": "saved",
            "metadata": {}, "timeout_seconds": 30})
    op = reduce_workflow_operations(log.read_all())["wop-settled"]
    ref = write_immutable_json_sidecar(state, {"verdict": "best_so_far"},
        root="operations/results", kind="run_manager_reflection", schema_version="test.v1", created_by="test")
    service.settle(operation_id=op["operation_id"], request_hash=op["request_hash"],
                   workflow_run_id="run", admitted_call_result_ref=ref)
    for _ in range(2):
        reconcile_reflections(state_dir=state, writer=writer, invoke=lambda _: pytest.fail("must reuse result"))
    completed = [e for e in log.read_all() if e.type == COMPLETED]
    assert len(completed) == 1 and completed[0].payload["verdict"] == "best_so_far"


def test_required_request_digest_mismatch_is_explicit_failure(tmp_path):
    state, log, writer = _state(tmp_path)
    writer.emit("dispatch.paused", actor="operator")
    submit_reflection(state_dir=state, writer=writer, request_id="corrupt", prompt="saved",
                      metadata={}, timeout_seconds=30, invoke=lambda _: pytest.fail("must not invoke"))
    op = reduce_workflow_operations(log.read_all())["wop-corrupt"]
    (state / op["request_ref"]["ref"]).write_text("{}")
    reconcile_reflections(state_dir=state, writer=writer, invoke=lambda _: pytest.fail("must not invent inputs"))
    assert reduce_workflow_operations(log.read_all())["wop-corrupt"]["status"] == "failed"
    completed = [e for e in log.read_all() if e.type == COMPLETED]
    assert completed[0].payload["reason"] == "reflection_request_unreadable"


def test_cancelled_run_closes_request_without_provider(tmp_path):
    state, log, writer = _state(tmp_path)
    writer.emit("run.goal.started", payload={"run_id": "run-1"})
    writer.emit("run.cancelled", payload={"run_id": "run-1"})
    submit_reflection(state_dir=state, writer=writer, request_id="cancelled", prompt="saved",
                      metadata={"workflow_run_id": "run-1"}, timeout_seconds=30,
                      invoke=lambda _: pytest.fail("cancelled run"))
    completed = [e for e in log.read_all() if e.type == COMPLETED]
    assert len(completed) == 1 and completed[0].payload["status"] == "cancelled"


def test_generic_resume_does_not_cancel_rm_owned_handler(tmp_path):
    from zf.runtime.workflow_resume import build_workflow_operation_resume_checkpoints
    state, log, writer = _state(tmp_path)
    writer.emit("dispatch.paused", actor="operator")
    submit_reflection(state_dir=state, writer=writer, request_id="owned", prompt="fixed",
                      metadata={}, timeout_seconds=30, invoke=lambda _: pytest.fail("paused"))
    checkpoints = build_workflow_operation_resume_checkpoints(state, events=log.read_all())
    assert len(checkpoints) == 1
    assert checkpoints[0].safe_resume_action == "no_action"
    assert "Run Manager" in checkpoints[0].reason


def test_disabled_reflection_consumer_closes_queued_request_explicitly(tmp_path):
    state, log, writer = _state(tmp_path)
    writer.emit("dispatch.paused", actor="operator")
    submit_reflection(state_dir=state, writer=writer, request_id="disabled", prompt="fixed",
                      metadata={}, timeout_seconds=30, invoke=lambda _: pytest.fail("paused"))
    reconcile_reflections(state_dir=state, writer=writer, dispatch_enabled=False,
                          invoke=lambda _: pytest.fail("disabled consumer"))
    result = [e for e in log.read_all() if e.type == COMPLETED][-1]
    assert result.payload["status"] == "cancelled"
    assert result.payload["reason"] == "reflection_disabled"


def test_provider_completion_and_tick_replay_publish_only_one_receipt(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    state, log, writer = _state(tmp_path)
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=writer)
    service.ensure_operation(workflow_run_id="run", operation_id="wop-race",
        operation_type="run_manager_reflect", request={"request_id": "race", "prompt": "saved",
            "metadata": {}, "timeout_seconds": 30})
    op = reduce_workflow_operations(log.read_all())["wop-race"]
    ref = write_immutable_json_sidecar(state, {"verdict": "best_so_far"},
        root="operations/results", kind="run_manager_reflection", schema_version="test.v1", created_by="test")
    service.settle(operation_id=op["operation_id"], request_hash=op["request_hash"],
                   workflow_run_id="run", admitted_call_result_ref=ref)
    original = writer.emit
    def delayed_emit(kind, **kwargs):
        if kind == COMPLETED:
            sleep(0.04)  # Widen the actual check/append race, without a fake state reducer.
        return original(kind, **kwargs)
    monkeypatch.setattr(writer, "emit", delayed_emit)
    def replay(_):
        reconcile_reflections(state_dir=state, writer=writer, invoke=lambda _: pytest.fail("settled"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(replay, range(2)))
    assert len([e for e in log.read_all() if e.type == COMPLETED]) == 1
