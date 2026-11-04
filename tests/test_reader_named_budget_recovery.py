"""A budget-blocked Planner reader needs a named, replayable amendment."""

from argparse import Namespace

import pytest

from tests.test_reader_fanout_runtime import _durable_reader_state
from zf.cli.goal import _run_set
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_budget_guard import _operation_limits
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_resume_apply import apply_workflow_resume


@pytest.mark.parametrize("stopped_before_transport", [False, True])
@pytest.mark.parametrize("mismatch", [None, "request_hash", "attempt_id", "role_instance"])
def test_named_budget_amendment_resumes_original_planner_reader(tmp_path, monkeypatch, stopped_before_transport, mismatch):
    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path)
    original = dict(child["payload"])
    operation_id = original["operation_id"]
    writer = EventWriter(log)
    before = reduce_workflow_operations(log.read_all())[operation_id]
    exceeded = writer.append(ZfEvent(
        type="workflow.budget.exceeded", actor="zf-cli", correlation_id="run-durable",
        payload={"scope": "operation", "scope_id": operation_id,
                 "workflow_run_id": "run-durable", "exceeded_dimensions": ["tokens"],
                 "measurement": {"total_tokens": 1500}},
    ))
    WorkflowOperationService(state_dir=state, event_log=log, event_writer=writer).block(
        operation_id=operation_id, request_hash=original["request_hash"],
        workflow_run_id="run-durable", reason="workflow_budget_exceeded:tokens",
        causation_id=exceeded.id, correlation_id="run-durable",
    )
    monkeypatch.setattr("zf.cli.goal._context", lambda _args: Namespace(state_dir=state, config=config))
    monkeypatch.setattr("zf.cli.goal._projection", lambda *_args: {"run_id": "run-durable"})
    monkeypatch.setattr("zf.runtime.workflow_resume._task_attempt_rows", lambda _state: [])
    monkeypatch.setattr("zf.runtime.workflow_resume_apply._task_attempt_rows", lambda _state: [])
    assert _run_set(Namespace(objective="", status="active", operation=operation_id,
                              operation_token_budget=3000, reason="approve test continuation")) == 0
    checkpoint = next(c for c in build_workflow_resume_projection(state, config)["operation_checkpoints"]
                      if c["operation_id"] == operation_id)
    assert checkpoint["safe_resume_action"] == "redrive_budget_amended_reader_operation"
    assert apply_workflow_resume(state, config, checkpoint_id=checkpoint["checkpoint_id"])["applied"] == 1
    if stopped_before_transport:
        WorkflowOperationService(state_dir=state, event_log=log, event_writer=writer).interrupt(
            operation_id=operation_id, request_hash=original["request_hash"],
            workflow_run_id="run-durable", reason="graceful_stop",
            source_attempt_id=before["active_attempt_id"],
        )
        checkpoint = next(c for c in build_workflow_resume_projection(state, config)["operation_checkpoints"]
                          if c["operation_id"] == operation_id)
        assert checkpoint["safe_resume_action"] == "redrive_interrupted_budget_reader_operation"
        assert apply_workflow_resume(state, config, checkpoint_id=checkpoint["checkpoint_id"])["applied"] == 1
    count = len(transport.sent)
    if mismatch:
        dispatch = next(e for e in log.read_all() if e.type == "fanout.child.dispatched")
        payload = dict(dispatch.payload)
        payload["payload"] = dict(payload.get("payload") or {})
        payload[mismatch] = "wrong-identity"
        if mismatch in {"request_hash", "attempt_id"}:
            payload["payload"][mismatch] = "wrong-identity"
        writer.append(ZfEvent(type=dispatch.type, actor="zf-cli", payload=payload))
        assert not runtime._recover_lost_reader_fanout_dispatches(log.read_all())
        assert len(transport.sent) == count
        return
    assert runtime._recover_lost_reader_fanout_dispatches(log.read_all())
    after = reduce_workflow_operations(log.read_all())[operation_id]
    assert after["status"] == "running"
    assert len(transport.sent) == count + 1
    for key in ("request_hash", "request_ref", "active_attempt_id", "lease_id"):
        assert after[key] == before[key]
    assert _operation_limits(state, after, events=log.read_all())["token_budget"] == 3000
    assert not runtime._recover_lost_reader_fanout_dispatches(log.read_all())
