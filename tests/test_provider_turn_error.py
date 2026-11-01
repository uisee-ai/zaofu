"""Provider errors close exact task attempts without inventing Verify verdicts."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json

import pytest

from tests.runtime.test_provider_stop_recovery import _StubTransport
from zf.core.config.schema import ProjectConfig, RoleConfig, WorkflowConfig, WorkflowTaskAttemptConfig, ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.provider_stop import classify_provider_stop
from zf.runtime.session_tailer import CodexSessionTailer
from zf.runtime.workflow_operation import WorkflowOperationService, load_workflow_operation


def runtime_case(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    TaskStore(state / "kanban.json").add(Task(
        id="T1", status="in_progress", assigned_to="verify-1", active_dispatch_id="dispatch-1",
    ))
    config = ZfConfig(project=ProjectConfig(name="errors", state_dir=str(state)),
        roles=[RoleConfig(name="verify-1", instance_id="verify-1", backend="mock")],
        workflow=WorkflowConfig(task_attempt=WorkflowTaskAttemptConfig(mode="enforce")))
    transport = _StubTransport()
    runtime = Orchestrator(state, config, transport)
    service = WorkflowOperationService(state_dir=state, event_log=runtime.event_log,
        event_writer=runtime.event_writer)
    ensured = service.ensure_operation(workflow_run_id="run-1", operation_id="op-1",
        operation_type="task-stage", task_id="T1", role_instance="verify-1",
        request={"task_pipeline_stage": "verify", "operation_generation": 1,
                 "task_map_generation": "map-1"})
    now = datetime.now(timezone.utc)
    store = TaskAttemptStore(state / "task_attempts.json")
    attempt = store.ensure_for_dispatch(run_id="run-1", task_id="T1",
        dispatch_id="dispatch-1", role="verify-1", instance_id="verify-1",
        operation_id="op-1", briefing_ref="briefing.md", created_at=now.isoformat(),
        lease_expires_at=(now + timedelta(hours=1)).isoformat(), max_attempts=3,
        identity_version="operation-v2", placement_epoch=1).attempt
    store.claim_delivery(attempt["attempt_id"], updated_at=now.isoformat())
    store.mark_sent(attempt["attempt_id"], updated_at=now.isoformat())
    service.mark_started(operation_id="op-1", request_hash=ensured.request_hash,
        workflow_run_id="run-1", task_id="T1", dispatch_id="dispatch-1",
        role_instance="verify-1", active_attempt_id=attempt["attempt_id"], lease_id=attempt["lease_id"])
    runtime.event_writer.append(ZfEvent(type="codex.hook.user_prompt_submit", actor="verify-1",
        payload={"turn_id": "turn-1", "session_id": "session-1"}))
    return runtime, store, attempt, transport


def emit_error(runtime, *, actor="verify-1", turn_id="turn-1", code="cyber_policy"):
    CodexSessionTailer(runtime.event_log)._emit_from_line(actor, json.dumps({
        "type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn_id,
        "last_agent_message": None, "error": {"codex_error_info": code,
            "message": ("Flagged by policy. Authorized users may contact support. private-body"
                        if code == "cyber_policy" else "connection failed")}},
    }))
    return runtime.event_log.read_all()[-1]


def test_tailer_preserves_error_status_without_copying_error_body(tmp_path):
    runtime, _, _, _ = runtime_case(tmp_path)
    event = emit_error(runtime)
    assert event.payload["provider_status"] == "failed"
    assert event.payload["provider_stop_reason"] == "provider_policy_rejected"
    assert "private-body" not in json.dumps(event.payload)


def test_structured_policy_code_takes_precedence_over_message_keywords():
    assert classify_provider_stop({"error": {"codex_error_info": "cyber_policy",
        "message": "Authorized security work requires access"}}) == "provider_policy_rejected"


def test_run_once_blocks_policy_rejection_and_closes_attempt_once(tmp_path):
    runtime, store, attempt, transport = runtime_case(tmp_path)
    event = emit_error(runtime)
    runtime.run_once(events=[event])
    assert load_workflow_operation(runtime.event_log, "op-1")["status"] == "blocked"
    assert store.get(attempt["attempt_id"])["status"] == "failed"
    assert store.get(attempt["attempt_id"])["retryable"] is False
    assert runtime.task_store.get("T1").status == "blocked"
    runtime.run_once(events=[event])
    runtime.run_once(events=[])
    events = runtime.event_log.read_all()
    assert len([e for e in events if e.type == "workflow.operation.blocked"]) == 1
    assert not transport.sends
    assert not [e for e in events if e.type in {
        "task.attempt.retry_scheduled", "task.pipeline.verify.completed", "task.done"}]


@pytest.mark.parametrize("mismatch", ["actor", "turn", "new_prompt", "old_close", "settled"])
def test_closed_turn_cannot_affect_other_or_finished_attempt(tmp_path, mismatch):
    runtime, store, attempt, _ = runtime_case(tmp_path)
    event = emit_error(runtime, actor="other" if mismatch == "actor" else "verify-1",
        turn_id="old-turn" if mismatch == "turn" else "turn-1")
    if mismatch == "new_prompt":
        runtime.event_writer.append(ZfEvent(type="codex.hook.user_prompt_submit", actor="verify-1",
            payload={"turn_id": "turn-2", "session_id": "session-1"}))
    if mismatch == "old_close":
        event = replace(event, ts="2020-01-01T00:00:00+00:00")
    if mismatch == "settled":
        runtime.event_writer.append(ZfEvent(type="workflow.operation.settled", task_id="T1",
            payload={"operation_id": "op-1", "workflow_run_id": "run-1"}))
    runtime.run_once(events=[event])
    assert load_workflow_operation(runtime.event_log, "op-1")["status"] == (
        "settled" if mismatch == "settled" else "running")
    assert not [e for e in runtime.event_log.read_all() if e.type == "provider.stop.recovery"]


def test_transport_error_uses_existing_run_manager_recovery(tmp_path):
    runtime, store, attempt, transport = runtime_case(tmp_path)
    event = emit_error(runtime, code="connection_failed")
    runtime.run_once(events=[event])
    assert load_workflow_operation(runtime.event_log, "op-1")["status"] == "suspended"
    row = store.get(attempt["attempt_id"])
    assert row["recovery_owner"] == "run_manager"
    assert row["retryable"] is True
    assert not transport.sends


def test_fanout_does_not_treat_policy_refusal_as_lost_dispatch(tmp_path):
    from tests.test_reader_fanout_runtime import _start_fanout, _state

    _, log, transport, runtime = _state(tmp_path,
        child_success_event="workflow.child.completed", child_failure_event="workflow.child.failed")
    _start_fanout(runtime)
    event = emit_error(runtime, actor="review-a")
    runtime.run_once(events=[event])
    assert not [e for e in log.read_all() if e.type in {
        "fanout.child.dispatch_lost", "fanout.child.redispatched"}]
