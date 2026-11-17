"""Completed Codex turns do not require a human to type continue."""
from datetime import datetime, timezone

import pytest

from tests.test_provider_turn_error import runtime_case
from zf.core.events import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.workflow_operation import load_workflow_operation


def case(tmp_path):
    runtime, store, attempt, transport = runtime_case(tmp_path)
    runtime.config.roles[0].publishes = ["verify.passed"]
    # The fixture has no domain inputs; declare that explicitly through the kernel.
    runtime.event_writer.append(ZfEvent(type="task.dispatched", task_id="T1", origin="kernel",
        payload={"attempt_id": attempt["attempt_id"], "dispatch_id": "dispatch-1", "required_reads": []}))
    event = runtime.event_writer.append(ZfEvent(type="provider.turn.closed", actor="verify-1",
        payload={"backend": "codex", "provider_status": "completed", "turn_id": "turn-1"}))
    return runtime, store, attempt, transport, event


def test_idle_tick_recovers_lost_closed_turn_notification_without_restarting_attempt(tmp_path):
    runtime, store, attempt, transport, event = case(tmp_path)
    runtime.run_once(events=[])
    assert len(transport.sends) == 1
    assert store.get(attempt["attempt_id"])["status"] == "sent"
    assert load_workflow_operation(runtime.event_log, "op-1")["status"] == "running"
    assert "Resume or complete" in transport.sends[0][1].read_text()
    restarted = Orchestrator(runtime.state_dir, runtime.config, transport)
    restarted.run_once(events=[event])
    restarted.run_once(events=[])
    assert len(transport.sends) == 1
    from zf.runtime.run_manager_progress import task_evidence_signals
    signal = task_evidence_signals(restarted.event_log.read_all(), ["T1"])["T1"]["continuation"]
    assert signal["delivery_status"] == "delivered"
    assert signal["business_completed"] is False


@pytest.mark.parametrize("waiting", ["pause", "tool", "assistance", "new_turn", "expired", "settled"])
def test_current_external_wait_or_old_turn_cannot_launch_continuation(tmp_path, waiting):
    runtime, store, attempt, transport, event = case(tmp_path)
    if waiting == "pause":
        runtime.event_writer.append(ZfEvent(type="dispatch.paused", actor="operator"))
    if waiting == "tool":
        runtime.event_writer.append(ZfEvent(type="agent.tool.use", actor="verify-1",
            payload={"tool_use_id": "long-test", "tool": "exec"}))
    if waiting == "assistance":
        runtime.event_writer.append(ZfEvent(type="worker.assistance.requested", actor="verify-1", task_id="T1",
            payload={"request_id": "help", "active_attempt_id": attempt["attempt_id"], "operation_id": "op-1"}))
    if waiting == "new_turn":
        runtime.event_writer.append(ZfEvent(type="codex.hook.user_prompt_submit", actor="verify-1",
            payload={"turn_id": "turn-2", "session_id": "session-1"}))
    if waiting == "expired":
        store.renew_lease(attempt["attempt_id"], lease_expires_at="2000-01-01T00:00:00+00:00",
                         updated_at=datetime.now(timezone.utc).isoformat())
    if waiting == "settled":
        runtime.event_writer.append(ZfEvent(type="workflow.operation.settled", task_id="T1",
            payload={"operation_id": "op-1", "workflow_run_id": "run-1"}))
    runtime.run_once(events=[event])
    assert not transport.sends


def test_failed_followup_preserves_original_attempt_and_dispatch(tmp_path):
    runtime, store, attempt, transport, _ = case(tmp_path)
    def failed(*args, **kwargs):
        raise OSError("delivery outcome unknown")
    transport.send_task = failed
    runtime.run_once(events=[])
    assert runtime.task_store.get("T1").active_dispatch_id == "dispatch-1"
    assert store.get(attempt["attempt_id"])["status"] == "sent"
    events = runtime.event_log.read_all()
    assert any(e.type == "worker.continuation.claimed" for e in events)
    result = [e for e in events if e.type == "worker.stuck.recovered"][-1]
    assert result.payload["prompt_injected"] is False
    assert "unknown" in result.payload["prompt_error"]


def test_interrupted_claim_is_not_blindly_resent_after_restart(tmp_path):
    from zf.runtime.task_attempt_continuation import claim_completion_nudge
    runtime, _, _, transport, event = case(tmp_path)
    assert claim_completion_nudge(runtime, "verify-1", "T1", "dispatch-1",
                                  causation_id=event.id, correlation_id="run-1")
    restarted = Orchestrator(runtime.state_dir, runtime.config, transport)
    restarted.run_once(events=[])
    assert not transport.sends
    from zf.runtime.run_manager_progress import task_evidence_signals
    obligation = task_evidence_signals(restarted.event_log.read_all(), ["T1"])["T1"]["continuation"]
    assert obligation["delivery_status"] == "unknown"
    assert obligation["recovery_owner"] == "run_manager"
    assert obligation["next_action"] == "reconcile_existing_turn_before_retry"


def test_pending_owner_approval_prevents_continuation(tmp_path):
    runtime, _, _, transport, _ = case(tmp_path)
    runtime.event_writer.append(ZfEvent(type="approval.requested", task_id="T1",
        payload={"approval_ref": "review-current"}))
    runtime.run_once(events=[])
    assert not transport.sends


def test_continuation_does_not_bypass_budget_or_restart_missing_worker(tmp_path, monkeypatch):
    runtime, _, _, transport, _ = case(tmp_path)
    monkeypatch.setattr(runtime, "_budget_exceeded", lambda role: True)
    runtime.run_once(events=[])
    assert not transport.sends
    assert runtime.task_store.get("T1").active_dispatch_id == "dispatch-1"


def test_previous_turn_background_tool_still_blocks_followup(tmp_path):
    from dataclasses import replace
    runtime, _, _, transport, event = case(tmp_path)
    runtime.event_writer.append(ZfEvent(type="agent.tool.use", actor="verify-1", payload={"tool_use_id": "still-running"}))
    runtime.event_writer.append(ZfEvent(type="codex.hook.user_prompt_submit", actor="verify-1",
        payload={"session_id": "session-1", "turn_id": "turn-2"}))
    runtime.event_writer.append(replace(event, id="second-close", payload={**event.payload, "turn_id": "turn-2"},
                                       ts=datetime.now(timezone.utc).isoformat()))
    runtime.run_once(events=[])
    assert not transport.sends
