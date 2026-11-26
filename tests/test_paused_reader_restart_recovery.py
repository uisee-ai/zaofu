"""Paused runs and failed dispatches must not exhaust reader recovery."""

import pytest

from tests.test_reader_fanout_runtime import _durable_reader_state
from tests.test_reader_respawn_continuation import _running_recovered_synth
from zf.core.events.writer import EventWriter
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_operation import WorkflowOperationService, load_workflow_operation


def _event(log, kind, **payload):
    log.append(ZfEvent(type=kind, actor="zf-cli", payload=payload))


@pytest.mark.parametrize("kind", ["child", "synth"])
def test_paused_run_restart_does_not_consume_recovery(tmp_path, kind):
    _state, log, runtime, role, dispatch, _before = _running_recovered_synth(
        tmp_path, kind=kind
    )
    run_id = dispatch.payload.get("workflow_run_id") or dispatch.correlation_id
    _event(log, "run.paused", run_id=run_id)
    _event(log, "worker.launch_artifact.written", instance_id=role.instance_id, launch_attempt=2)
    baseline = len(log.read_all())
    recover = (runtime._recover_lost_fanout_synth_dispatches if kind == "synth"
               else runtime._recover_lost_reader_fanout_dispatches)
    assert not recover(log.read_all())
    assert not recover(log.read_all())
    unexpected = {"fanout.child.dispatch_lost", "fanout.child.failed", "fanout.synth.completed", "workflow.operation.failed"}
    assert not [e.type for e in log.read_all()[baseline:] if e.type in unexpected]


def test_blocked_retry_does_not_count_as_delivered_recovery(tmp_path, monkeypatch):
    _state, log, _transport, runtime, _config, child = _durable_reader_state(tmp_path)
    _event(log, "worker.launch_artifact.written", instance_id=child["role_instance"], launch_attempt=2)
    original = runtime._retry_fanout_child
    monkeypatch.setattr(runtime, "_retry_fanout_child", lambda **_kwargs: False)
    assert runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    monkeypatch.setattr(runtime, "_retry_fanout_child", original)
    assert runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    events = log.read_all()
    assert len([e for e in events if e.type == "fanout.child.dispatch_lost"]) == 1
    assert not [e for e in events if e.type == "fanout.child.failed"]
    assert len([e for e in events if e.type == "fanout.child.dispatched"]) == 2


def test_resume_after_paused_restart_reuses_original_request(tmp_path):
    state, log, _transport, runtime, _config, child = _durable_reader_state(tmp_path)
    identity = child["payload"]
    initial = load_workflow_operation(log, identity["operation_id"])
    _event(log, "run.paused", run_id="run-durable")
    WorkflowOperationService(state_dir=state, event_log=log, event_writer=EventWriter(log)).interrupt(
        operation_id=identity["operation_id"], request_hash=identity["request_hash"],
        workflow_run_id="run-durable", reason="graceful_stop", task_id="T-VERIFY",
        source_attempt_id=initial["active_attempt_id"],
    )
    _event(log, "worker.launch_artifact.written", instance_id=child["role_instance"], launch_attempt=2)
    assert not runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    _event(log, "run.resumed", run_id="run-durable")
    assert runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    resumed = load_workflow_operation(log, identity["operation_id"])
    assert resumed["status"] == "running"
    for key in ("request_hash", "active_attempt_id", "lease_id"):
        assert resumed[key] == initial[key]
    assert not [e for e in log.read_all() if e.type == "fanout.child.failed"]


def test_pausing_another_run_does_not_block_reader_recovery(tmp_path):
    _state, log, _transport, runtime, _config, child = _durable_reader_state(tmp_path)
    _event(log, "run.paused", run_id="another-run")
    _event(log, "worker.launch_artifact.written", instance_id=child["role_instance"], launch_attempt=2)
    assert runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    assert len([e for e in log.read_all() if e.type == "fanout.child.dispatched"]) == 2


def test_paused_run_does_not_timeout_while_controller_is_running(tmp_path, monkeypatch):
    _state, log, _transport, runtime, _config, _child = _durable_reader_state(tmp_path)
    _event(log, "run.paused", run_id="run-durable")
    monkeypatch.setattr(runtime, "_now", lambda: 9_999_999_999.0)
    baseline = len(log.read_all())
    runtime._check_fanout_timeouts()
    assert not [e.type for e in log.read_all()[baseline:]
                if e.type in {"fanout.child.failed", "fanout.timed_out", "fanout.synth.completed"}]


def test_resumed_run_gets_bounded_timeout_window(tmp_path, monkeypatch):
    import time
    from datetime import datetime, timezone

    _state, log, _transport, runtime, _config, _child = _durable_reader_state(tmp_path)
    base = time.time()
    for kind, seconds in (("run.paused", 10), ("run.resumed", 10000)):
        log.append(ZfEvent(type=kind, actor="zf-cli", payload={"run_id": "run-durable"},
                           ts=datetime.fromtimestamp(base + seconds, timezone.utc).isoformat()))
    monkeypatch.setattr(runtime, "_now", lambda: base + 10001)
    runtime._check_fanout_timeouts()
    assert not [e for e in log.read_all() if e.type == "fanout.child.failed"]
    monkeypatch.setattr(runtime, "_now", lambda: base + 20000)
    runtime._check_fanout_timeouts()
    assert [e for e in log.read_all() if e.type == "fanout.child.failed"]


def test_synth_timeout_waits_for_run_resume(tmp_path, monkeypatch):
    import time
    from datetime import datetime, timezone

    _state, log, runtime, _role, dispatch, _before = _running_recovered_synth(tmp_path)
    run_id = dispatch.payload.get("workflow_run_id") or dispatch.correlation_id
    base = time.time()
    for kind, seconds in (("run.paused", 10), ("run.resumed", 10000)):
        if kind == "run.resumed":
            monkeypatch.setattr(runtime, "_now", lambda: base + 9999)
            runtime._check_fanout_timeouts()
            assert not [e for e in log.read_all() if e.type == "fanout.synth.completed"]
        log.append(ZfEvent(type=kind, actor="zf-cli", payload={"run_id": run_id},
                           ts=datetime.fromtimestamp(base + seconds, timezone.utc).isoformat()))
    monkeypatch.setattr(runtime, "_now", lambda: base + 10001)
    runtime._check_fanout_timeouts()
    assert not [e for e in log.read_all() if e.type == "fanout.synth.completed"]
    monkeypatch.setattr(runtime, "_now", lambda: base + 20000)
    runtime._check_fanout_timeouts()
    assert [e for e in log.read_all() if e.type == "fanout.synth.completed"]


def test_pausing_another_run_does_not_freeze_timeout(tmp_path, monkeypatch):
    _state, log, _transport, runtime, _config, _child = _durable_reader_state(tmp_path)
    _event(log, "run.paused", run_id="another-run")
    monkeypatch.setattr(runtime, "_now", lambda: 9_999_999_999.0)
    runtime._check_fanout_timeouts()
    assert [e for e in log.read_all() if e.type == "fanout.child.failed"]


@pytest.mark.parametrize("scope", ["run", "project"])
def test_final_reader_result_survives_pause_and_releases_one_synth_on_resume(tmp_path, scope):
    from tests.test_reader_fanout_runtime import _task_map_synth_orchestrator
    from zf.runtime.orchestrator import Orchestrator

    state, log, runtime, fanout = _task_map_synth_orchestrator(tmp_path, complete_child=False)
    run = "trace-product"
    hold = runtime.event_writer.append(ZfEvent(type="run.paused" if scope == "run" else "dispatch.paused",
                                              actor="zf-cli", payload={"run_id": run} if scope == "run" else {}))
    runtime.run_once(events=[hold])
    child = runtime.event_writer.append(ZfEvent(
        type="task_map.child.completed", actor="source-reader", correlation_id=run,
        payload={"fanout_id": fanout, "child_id": "source-reader", "run_id": f"run-{fanout}-source-reader",
                 "status": "completed", "summary": "Source contract reviewed", "artifact_refs": ["docs/product-prd.md"]},
    ))
    runtime.run_once(events=[child])
    # Simulate a watcher replacement between the accepted child and its successor.
    runtime = Orchestrator(state, runtime.config, runtime.transport)
    unrelated = runtime.event_writer.append(ZfEvent(type="run.resumed", actor="zf-cli", payload={"run_id":"other-run"}))
    runtime.run_once(events=[unrelated])
    runtime.run_once(events=[])
    observed = log.read_all()
    assert sum(e.type == "fanout.child.completed" and e.payload["fanout_id"] == fanout for e in observed) == 1
    assert not [e for e in observed if e.type in {"fanout.aggregate.started", "fanout.synth.dispatched", "fanout.synth.completed",
                                                 "task_map.blocked", "workflow.operation.blocked"}
                or e.type == "workflow.operation.requested" and e.payload.get("operation_type") == "fanout_synth"]
    resume = runtime.event_writer.append(ZfEvent(type="run.resumed" if scope == "run" else "dispatch.resumed",
                                                actor="zf-cli", payload={"run_id": run} if scope == "run" else {}))
    runtime.run_once(events=[resume])
    assert sum(e.type == "fanout.synth.dispatched" for e in log.read_all()) == 1
    runtime.run_once(events=[resume, child])
    runtime.run_once(events=[])
    observed = log.read_all()
    assert sum(e.type == "fanout.aggregate.started" for e in observed) == 1
    requests = [e for e in observed if e.type == "workflow.operation.requested" and e.payload.get("operation_type") == "fanout_synth"]
    dispatches = [e for e in observed if e.type == "fanout.synth.dispatched"]
    assert len(dispatches) == len(requests) == 1
    assert dispatches[0].payload["operation_id"] == requests[0].payload["operation_id"]
    assert sum(sent[0] == "task-map-synth" for sent in runtime.transport.sent) == 1
    assert not [e for e in observed if e.type in {"fanout.synth.completed", "task_map.blocked", "workflow.operation.blocked"}]


def test_prd_planner_can_submit_during_hold_and_exact_review_resumes(tmp_path, monkeypatch):
    from tests import test_pre_impl_revision_owner_gate as prd

    submit = prd._submit_review_operation

    def pause_before_planner_submit(runtime, role, decide, **kwargs):
        if role == "planner":
            request = next(e for e in runtime.event_log.query(type="workflow.operation.requested")
                           if e.payload["role_instance"] == role)
            runtime.event_writer.append(ZfEvent(type="run.paused", actor="zf-cli",
                                                payload={"run_id": request.payload["workflow_run_id"]}))
        return submit(runtime, role, decide, **kwargs)

    monkeypatch.setattr(prd, "_submit_review_operation", pause_before_planner_submit)
    runtime, planner, previous, _, _, transport = prd.pending_planner(tmp_path, monkeypatch)
    runtime.run_once(events=[planner])
    runtime.run_once(events=[])
    op = load_workflow_operation(runtime.event_log, planner.payload["operation_id"])
    assert op["status"] == "settled" and op["admitted_control_result_ref"]
    assert all(e.payload.get("role_instance") != "plan-critic" for e in runtime.event_log.query(type="workflow.operation.requested"))
    assert not runtime.event_log.query(type="prd.plan.failed")
    resume = runtime.event_writer.append(ZfEvent(type="run.resumed", actor="zf-cli", payload={"run_id":op["workflow_run_id"]}))
    runtime.run_once(events=[resume])
    runtime.run_once(events=[resume])
    assert [item[0] for item in transport.sent if item[0] == "plan-critic"] == ["plan-critic"]
    reviewed = prd.submit_critic(runtime)
    runtime.run_once(events=[reviewed])
    assert runtime.event_log.query(type="task_map.ready")[-1].id != previous.id
    assert not runtime.event_log.query(type="plan.approved")
