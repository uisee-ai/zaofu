"""A never-delivered reader request must not deadlock its idle provider's pin."""
from types import SimpleNamespace

import pytest

from tests.test_role_lifecycle_runtime import _runtime
from zf.runtime.environment_usage import EnvironmentInUse
from zf.runtime.reader_target_dispatch import pin_reader_target_or_defer
from zf.runtime.workflow_operation import WorkflowOperationService, load_workflow_operation
from zf.runtime.workdirs import WorkdirManager


@pytest.mark.parametrize("fault", ["", "running", "suspended", "prior_start", "prior_send",
    "other_attempt", "other_run", "other_stage", "writer", "active_turn", "unknown_transport"])
def test_pending_pin_releases_only_its_never_delivered_idle_provider(tmp_path, monkeypatch, fault):
    runtime, role, transport = _runtime(tmp_path)
    role.role_kind = "reader"
    transport.alive.add(role.instance_id)
    service = WorkflowOperationService(state_dir=runtime.state_dir,
        event_log=runtime.event_log, event_writer=runtime.event_writer)
    service.ensure_operation(workflow_run_id="other" if fault == "other_run" else "run",
        operation_id="op", operation_type="fanout_writer_child" if fault == "writer" else "fanout_reader_child",
        parent_stage_id="other" if fault == "other_stage" else "verify",
        role_instance=role.instance_id, active_attempt_id="other" if fault == "other_attempt" else "attempt",
        request={"prompt": "verify"})
    if fault in {"running", "suspended", "prior_start"}:
        runtime.event_writer.emit("workflow.operation.started", payload={"operation_id": "op"})
        if fault != "running":
            runtime.event_writer.emit("workflow.operation.interrupted", payload={"operation_id": "op"})
        if fault == "prior_start":
            runtime.event_writer.emit("workflow.operation.redrive_admitted", payload={"operation_id": "op"})
    if fault == "prior_send":
        runtime.event_writer.emit("fanout.child.dispatched", payload={"operation_id": "op",
            "role_instance": role.instance_id, "run_id": "attempt"})
    monkeypatch.setattr(runtime, "_active_provider_turn",
        lambda _: {"active": True} if fault == "active_turn" else None)
    if fault == "unknown_transport":
        monkeypatch.setattr(transport, "is_alive", lambda _: (_ for _ in ()).throw(RuntimeError("unknown")))
    def pin(*args):
        if role.instance_id in transport.alive:
            raise EnvironmentInUse("idle provider owns workspace")
        return "a" * 40
    monkeypatch.setattr(WorkdirManager, "pin_reader_target", pin)
    context = SimpleNamespace(stage_id="verify", trace_id="run", fanout_id="f")
    child = SimpleNamespace(child_id="reader", payload={})
    args = dict(role=role, target_ref="HEAD", context=context, child=child,
        run_id="attempt", causation_id="start")
    assert not pin_reader_target_or_defer(runtime, **args)
    if fault:
        assert not transport.terminated
    else:
        assert transport.terminated == [role.instance_id]
        assert pin_reader_target_or_defer(runtime, **args)
        assert child.payload["target_commit"] == "a" * 40
        assert load_workflow_operation(runtime.event_log, "op")["status"] == "requested"
