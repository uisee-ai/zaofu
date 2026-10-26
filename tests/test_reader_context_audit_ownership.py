"""Reader context recovery cannot reopen the implementation Task."""

import pytest

from tests.test_runtime_self_recovery_closures import _orchestrator, _state_dir
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.long_horizon import apply_completion_audit


def setup_reader(tmp_path, *, topology="fanout_reader"):
    runtime = _orchestrator(_state_dir(tmp_path))
    runtime.task_store.add(Task(id="T1", title="implemented product", status="in_progress",
                                assigned_to="dev", active_dispatch_id=""))
    runtime.event_writer.append(ZfEvent(type="fanout.started", actor="zf-cli",
        correlation_id="product-run", payload={"fanout_id": "verify", "stage_id": "verify",
            "topology": topology, "expected_children": ["reader"]}))
    scope = {"fanout_id": "verify", "stage_id": "verify", "child_id": "reader", "run_id": "read-1"}
    runtime.event_writer.append(ZfEvent(type="fanout.child.dispatched", actor="zf-cli",
        task_id="T1", correlation_id="product-run", payload={**scope, "role_instance": "verify-0"}))
    trigger = runtime.event_writer.append(ZfEvent(type="worker.context.critical", actor="verify-0",
        task_id="T1", correlation_id="product-run", payload={"dispatch_id": "read-1",
            "reason": "hard_cap_exceeded", "active_fanout": scope}))
    return runtime, trigger


def test_reader_context_critical_does_not_schedule_product_task_retry(tmp_path):
    runtime, trigger = setup_reader(tmp_path)
    decision = runtime._on_context_critical(trigger)
    assert decision.action == "skip"
    assert "reader fanout" in decision.reason
    assert not any(e.type in {"task.retry_scheduled", "completion_audit.routed"}
                   for e in runtime.event_log.read_all())
    assert runtime.task_store.get("T1").status == "in_progress"


def test_preupgrade_reader_retry_schedule_cannot_requeue_task(tmp_path):
    runtime, trigger = setup_reader(tmp_path)
    apply_completion_audit(state_dir=runtime.state_dir, task_id="T1",
        event_writer=runtime.event_writer, trigger_event=trigger, task_store=runtime.task_store,
        config=runtime.config, project_root=runtime.project_root, mutate=False)
    schedule = next(e for e in runtime.event_log.read_all() if e.type == "task.retry_scheduled")
    # The original attempt may already have been replaced when the schedule is consumed.
    runtime.event_writer.append(ZfEvent(type="fanout.child.dispatch_lost", actor="zf-cli",
        payload={"fanout_id": "verify", "child_id": "reader", "run_id": "read-1"}))
    for _ in range(2):
        assert runtime._on_completion_scheduled(schedule).action == "skip"
    assert runtime.task_store.get("T1").status == "in_progress"
    assert not any(e.type == "task.requeued" for e in runtime.event_log.read_all())


@pytest.mark.parametrize("mismatch", ["actor", "task", "run", "stage", "trace", "writer"])
def test_unproven_reader_owner_does_not_suppress_normal_context_audit(tmp_path, mismatch):
    runtime, trigger = setup_reader(tmp_path,
        topology="fanout_writer_scoped" if mismatch == "writer" else "fanout_reader")
    if mismatch == "actor":
        trigger.actor = "other"
    elif mismatch == "task":
        trigger.payload["task_id"] = "OTHER"
        trigger.task_id = "OTHER"
        runtime.task_store.add(Task(id="OTHER", title="other", status="in_progress"))
    elif mismatch == "run":
        trigger.payload["active_fanout"]["run_id"] = "read-2"
    elif mismatch == "stage":
        trigger.payload["active_fanout"]["stage_id"] = "other"
    elif mismatch == "trace":
        trigger.correlation_id = "other-run"
    runtime._on_context_critical(trigger)
    assert any(e.type == "completion_audit.routed" for e in runtime.event_log.read_all())
