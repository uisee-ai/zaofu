"""A provider gate before paste preserves the one bounded OA obligation."""
from pathlib import Path

import pytest

from tests.test_orchestrator_agent_operations import (
    RecordingTransport, _config, _request, _state,
)
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_agent_operations import CHECKPOINT_REQUESTED
from zf.runtime.orchestrator_agent_recovery import reconcile_orchestrator_agent_operation_liveness
from zf.runtime.orchestrator_agent_transport import (
    dispatch_orchestrator_agent_operation, dispatch_pending_orchestrator_checkpoint,
)
from zf.runtime.orchestrator_periodic_sweep import run_context_sweep
from zf.runtime.orchestrator_unsent_recovery import unsent_checkpoint_failure
from zf.runtime.tmux import TmuxError
from zf.runtime.workflow_operation import load_workflow_operation


class GateTransport(RecordingTransport):
    gate = True

    def send_task(self, *args, **kwargs):
        if self.gate:
            error = TmuxError("provider readiness failed")
            error.failure_class = "provider_interactive_blocked"
            error.interactive_prompt_marker = "login_required"
            error.input_not_sent = True
            raise error
        super().send_task(*args, **kwargs)

    def wait_ready(self, *args, **kwargs):
        return not self.gate


def failed_checkpoint(tmp_path: Path):
    state, log, writer = _state(tmp_path)
    transport = GateTransport()
    runtime = Orchestrator(state, _config(), transport, project_root=tmp_path)
    prepared = _request(runtime)
    checkpoint = next(e for e in log.read_all() if e.type == CHECKPOINT_REQUESTED)
    dispatch_orchestrator_agent_operation(runtime, runtime.config.roles[0], checkpoint)
    assert load_workflow_operation(log, prepared.operation_id)["status"] == "failed"
    return runtime, transport, prepared, checkpoint


def retries(runtime):
    return [e for e in runtime.event_log.read_all() if e.type == CHECKPOINT_REQUESTED
            and e.payload.get("unsent_failure_event_id")]


def test_periodic_then_lost_wake_resumes_original_once(tmp_path):
    runtime, transport, prepared, original = failed_checkpoint(tmp_path)
    failure = next(e for e in runtime.event_log.read_all() if e.type == "orchestrator.dispatch_failed")
    assert failure.payload["input_not_sent"] is True
    assert failure.payload["interactive_prompt_marker"] == "login_required"
    run_context_sweep(runtime)
    assert not retries(runtime)
    transport.gate = False
    run_context_sweep(runtime)
    run_context_sweep(runtime)
    retry, = retries(runtime)
    assert retry.payload["original_checkpoint_event_id"] == original.id
    assert retry.payload["operation_id"] == prepared.operation_id
    dispatch_pending_orchestrator_checkpoint(runtime)
    operation = load_workflow_operation(runtime.event_log, prepared.operation_id)
    assert operation["status"] == "running"
    assert operation["retry_count"] == 1
    assert operation["request_hash"] == prepared.request_hash
    assert operation["active_attempt_id"] == prepared.attempt_id
    assert len(transport.sent) == 1
    run_context_sweep(runtime)
    dispatch_pending_orchestrator_checkpoint(runtime)
    assert len(transport.sent) == len(retries(runtime)) == 1


def test_gate_returning_after_queue_blocks_without_retry_loop(tmp_path):
    runtime, transport, prepared, _ = failed_checkpoint(tmp_path)
    transport.gate = False
    reconcile_orchestrator_agent_operation_liveness(runtime)
    transport.gate = True
    dispatch_pending_orchestrator_checkpoint(runtime)
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "blocked"
    transport.gate = False
    reconcile_orchestrator_agent_operation_liveness(runtime)
    dispatch_pending_orchestrator_checkpoint(runtime)
    assert len(retries(runtime)) == 1
    assert not transport.sent


@pytest.mark.parametrize("field,value", [
    ("input_not_sent", False), ("failure_class", "unknown"),
    ("trigger_event_id", "another-checkpoint"), ("operation_id", "another-operation"),
])
def test_incomplete_or_contradictory_failure_is_not_retry_authority(tmp_path, field, value):
    runtime, _, prepared, _ = failed_checkpoint(tmp_path)
    events = runtime.event_log.read_all()
    failure = next(e for e in events if e.type == "orchestrator.dispatch_failed")
    failure.payload[field] = value
    operation = load_workflow_operation(runtime.event_log, prepared.operation_id)
    assert unsent_checkpoint_failure(runtime, operation, events) is None


def test_legacy_exact_presend_class_is_supported_but_not_error_text(tmp_path):
    runtime, _, prepared, _ = failed_checkpoint(tmp_path)
    events = runtime.event_log.read_all()
    failure = next(e for e in events if e.type == "orchestrator.dispatch_failed")
    del failure.payload["input_not_sent"]
    del failure.payload["interactive_prompt_marker"]
    operation = load_workflow_operation(runtime.event_log, prepared.operation_id)
    assert unsent_checkpoint_failure(runtime, operation, events).id == failure.id
    failure.payload["error"] = "provider_interactive_blocked input_not_sent=True"
    del failure.payload["failure_class"]
    assert unsent_checkpoint_failure(runtime, operation, events) is None


@pytest.mark.parametrize("event_type,payload", [
    ("dispatch.paused", {}),
    ("run.paused", {"workflow_run_id": "run-oa-1"}),
    ("run.cancelled", {"workflow_run_id": "run-oa-1"}),
    ("codex.hook.user_prompt_submit", {"session_id": "session", "turn_id": "turn"}),
])
def test_pause_terminal_or_busy_provider_does_not_recover(tmp_path, event_type, payload):
    runtime, transport, _, _ = failed_checkpoint(tmp_path)
    runtime.event_writer.append(ZfEvent(type=event_type, origin="kernel",
        actor="orchestrator", payload=payload, correlation_id="run-oa-1"))
    transport.gate = False
    reconcile_orchestrator_agent_operation_liveness(runtime)
    assert not retries(runtime)


def test_changed_configuration_blocks_unsent_replay(tmp_path):
    runtime, transport, _, _ = failed_checkpoint(tmp_path)
    runtime.config.project.name = "changed"
    transport.gate = False
    reconcile_orchestrator_agent_operation_liveness(runtime)
    assert not retries(runtime)


def test_two_dispatchers_share_one_retry_claim(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    runtime, transport, prepared, _ = failed_checkpoint(tmp_path)
    transport.gate = False
    reconcile_orchestrator_agent_operation_liveness(runtime)
    retry, = retries(runtime)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(dispatch_orchestrator_agent_operation,
                               runtime, runtime.config.roles[0], retry) for _ in range(2)]
        for future in futures:
            future.result()
    assert len(transport.sent) == 1
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["retry_count"] == 1


def test_current_pause_after_retry_queued_prevents_dispatch(tmp_path):
    runtime, transport, _, _ = failed_checkpoint(tmp_path)
    transport.gate = False
    reconcile_orchestrator_agent_operation_liveness(runtime)
    runtime.event_writer.append(ZfEvent(type="run.paused", actor="zf-cli", origin="kernel",
        payload={"workflow_run_id": "run-oa-1"}, correlation_id="run-oa-1"))
    dispatch_pending_orchestrator_checkpoint(runtime)
    assert not transport.sent


def test_new_candidate_with_same_plan_and_tasks_invalidates_old_checkpoint(tmp_path):
    from tests.test_candidate_aggregate_review import _mixed
    from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
    from zf.runtime.orchestrator_unsent_recovery import _current_request
    from zf.runtime.run_manager import run_manager_tick

    runtime, _, _ = _mixed(tmp_path)
    run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer, config=runtime.config,
        project_root=runtime.project_root, event_log=runtime.event_log, spawn_repairs=False)
    event = next(e for e in runtime.event_log.read_all() if e.type == "orchestrator.semantic.failure.requested")
    prepared = request_semantic_failure_checkpoint(runtime, event)
    operation = load_workflow_operation(runtime.event_log, prepared.operation_id)
    assert _current_request(runtime, operation, runtime.event_log.read_all())
    native = next(e for e in reversed(runtime.event_log.read_all()) if e.type == "candidate.ready")
    runtime.event_writer.append(ZfEvent(type=native.type, origin="kernel", actor="zf-cli",
        payload=dict(native.payload), correlation_id=native.correlation_id))
    assert not _current_request(runtime, operation, runtime.event_log.read_all())
