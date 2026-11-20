from types import SimpleNamespace

import pytest

from tests.test_orchestrator_agent_operations import _request, _runtime
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_operations import CHECKPOINT_REQUESTED, activate_orchestrator_agent_operation
from zf.runtime.orchestrator_agent_recovery import reconcile_orchestrator_agent_operation_liveness
from zf.runtime.orchestrator_agent_transport import dispatch_orchestrator_agent_operation, orchestrator_checkpoint_busy
from zf.runtime.workflow_operation import load_workflow_operation


def _start(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.transport = SimpleNamespace(is_alive=lambda role: True)
    prepared = _request(runtime)
    checkpoint = next(e for e in runtime.event_log.read_all() if e.type == CHECKPOINT_REQUESTED)
    activate_orchestrator_agent_operation(runtime, prepared, dispatch_id=checkpoint.id, causation_id=checkpoint.id)
    return runtime, prepared, checkpoint


def _turn(runtime, kind, turn="turn-1", actor="orchestrator"):
    return runtime.event_writer.append(ZfEvent(type=kind, actor=actor,
        payload={"session_id": "session-1", "turn_id": turn, "backend": "codex"}))


def test_closed_turn_without_result_retries_same_checkpoint_once_and_then_blocks(tmp_path):
    runtime, prepared, checkpoint = _start(tmp_path)
    _turn(runtime, "codex.hook.user_prompt_submit")
    closed = _turn(runtime, "provider.turn.closed")
    for _ in range(2):
        reconcile_orchestrator_agent_operation_liveness(runtime)
    requests = [e for e in runtime.event_log.read_all() if e.type == CHECKPOINT_REQUESTED]
    assert len(requests) == 2
    retry = requests[-1]
    assert retry.payload["operation_id"] == prepared.operation_id
    assert retry.payload["original_checkpoint_event_id"] == checkpoint.id
    assert retry.payload["missing_result_turn_event_id"] == closed.id
    assert retry.payload["checkpoint_dispatch_retry_attempt"] == 1
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "suspended"
    assert not any(e.type == "worker.respawn.requested" for e in runtime.event_log.read_all())

    # Exercise the actual transport caller, not just a new recovery helper.
    sent = []
    runtime._record_skill_provenance = lambda **kw: None
    runtime._dispatch_context = lambda **kw: None
    runtime._send_transport_task = lambda *args: sent.append(args)
    dispatch_orchestrator_agent_operation(runtime, runtime.config.roles[0], retry)
    assert len(sent) == 1
    assert "Resume the same checkpoint" in sent[0][2]
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["retry_count"] == 1
    _turn(runtime, "codex.hook.user_prompt_submit", turn="turn-2")
    _turn(runtime, "codex.hook.stop", turn="turn-2")
    for _ in range(2):
        reconcile_orchestrator_agent_operation_liveness(runtime)
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "blocked"
    assert not orchestrator_checkpoint_busy(runtime)
    assert len([e for e in runtime.event_log.read_all() if e.type == CHECKPOINT_REQUESTED]) == 2


@pytest.mark.parametrize("case", ["no_close", "other_role", "unknown_turn", "new_turn", "paused"])
def test_missing_result_recovery_needs_current_closed_turn_and_no_active_successor(tmp_path, case):
    runtime, prepared, _ = _start(tmp_path)
    _turn(runtime, "codex.hook.user_prompt_submit")
    if case != "no_close":
        _turn(runtime, "provider.turn.closed", turn="unknown" if case == "unknown_turn" else "turn-1",
              actor="other" if case == "other_role" else "orchestrator")
    if case == "new_turn":
        _turn(runtime, "codex.hook.user_prompt_submit", turn="new-turn")
    if case == "paused":
        runtime.event_writer.append(ZfEvent(type="loop.pause_requested", actor="operator"))
    reconcile_orchestrator_agent_operation_liveness(runtime)
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "running"
    assert len([e for e in runtime.event_log.read_all() if e.type == CHECKPOINT_REQUESTED]) == 1


@pytest.mark.parametrize("event_type", ["workflow.call.result.admitted", "run.goal.completed", "run.cancelled"])
def test_admitted_result_or_terminal_run_prevents_redrive_before_projection(tmp_path, event_type):
    runtime, prepared, _ = _start(tmp_path)
    _turn(runtime, "codex.hook.user_prompt_submit")
    _turn(runtime, "codex.hook.stop")
    runtime.event_writer.append(ZfEvent(type=event_type, actor="zf-cli", payload={
        "operation_id": prepared.operation_id, "request_hash": prepared.request_hash,
        "workflow_run_id": prepared.workflow_run_id,
    }))
    reconcile_orchestrator_agent_operation_liveness(runtime)
    assert len([e for e in runtime.event_log.read_all() if e.type == CHECKPOINT_REQUESTED]) == 1
