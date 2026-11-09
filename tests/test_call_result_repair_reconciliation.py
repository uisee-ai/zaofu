from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.config.schema import RoleConfig
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_correction import repair_instruction
from zf.runtime.call_result_repair_reconciliation import reconcile_call_result_repairs
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_agent_operations import activate_orchestrator_agent_operation
from zf.runtime.result_submit import (
    ResultSubmitError, SemanticResultSubmitService, provision_role_submit_credential,
)
from zf.runtime.workflow_operation import load_workflow_operation
from tests.test_orchestrator_agent_operations import (
    RecordingTransport, _config, _decision, _request, _runtime, _state,
)
from tests.test_result_submit import _running_workflow_read_operation


def _rejected(runtime):
    token = provision_role_submit_credential(runtime.state_dir, "orchestrator")
    prepared = _request(runtime)
    activate_orchestrator_agent_operation(runtime, prepared, dispatch_id="dispatch-1",
                                          causation_id="evt-run-start")
    submitter = SemanticResultSubmitService(state_dir=runtime.state_dir,
        event_log=runtime.event_log, event_writer=runtime.event_writer)
    kwargs = dict(operation_id=prepared.operation_id, semantic_result=_decision(prepared),
                  role_instance="orchestrator", credential=token.read_text().strip())
    with pytest.raises(ResultSubmitError, match="required_read_missing"):
        submitter.submit(**kwargs)
    runtime.config.roles[0].backend = "codex"
    return prepared, submitter, kwargs


def _stop(runtime):
    return runtime.event_writer.append(ZfEvent(type="codex.hook.stop", actor="orchestrator"))


def _capture(runtime):
    sent = []
    contexts = []
    runtime._dispatch_context = lambda **kwargs: contexts.append(kwargs)
    runtime.correction_contexts = contexts
    runtime._send_transport_task = lambda role, path, prompt, context: sent.append(path.read_text())
    return sent


def test_semantic_rejection_waits_for_stop_then_repairs_same_operation(tmp_path: Path):
    runtime = _runtime(tmp_path)
    prepared, submitter, kwargs = _rejected(runtime)
    sent = _capture(runtime)
    assert reconcile_call_result_repairs(runtime) == 0
    _stop(runtime)
    assert reconcile_call_result_repairs(runtime) == 1
    assert "Taskless operation protocol correction" in sent[0]
    assert "Active task:" not in sent[0]
    assert "never edit required_reads" in sent[0]
    assert f"result submit --operation {prepared.operation_id}" in sent[0]
    assert runtime.correction_contexts[0]["operation_id"] == prepared.operation_id
    assert runtime.correction_contexts[0]["run_id"] == prepared.workflow_run_id
    assert runtime.correction_contexts[0]["dispatch_id"] == "dispatch-1"
    assert reconcile_call_result_repairs(runtime) == 0
    # A new runtime object replays the ledger, not an in-memory delivered set.
    (tmp_path / "other").mkdir()
    runtime2 = _runtime(tmp_path / "other")
    runtime2.state_dir = runtime.state_dir
    runtime2.event_log = runtime.event_log
    runtime2.event_writer = runtime.event_writer
    runtime2.config = runtime.config
    _capture(runtime2)
    assert reconcile_call_result_repairs(runtime2) == 0
    for source in prepared.context.source_manifest["sources"]:
        read_attempt_artifact(runtime.state_dir, manifest=prepared.context.source_manifest,
            source_id=source["source_id"], artifact_id=source["artifact_id"],
            actor="orchestrator", role="orchestrator", provider="mock")
    submitter.submit(**kwargs)
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "settled"
    assert reconcile_call_result_repairs(runtime) == 0


@pytest.mark.parametrize("guard", ["settled", "cancelled", "hash", "attempt", "dispatch", "run_complete", "run_cancel", "run_pause", "pause", "exhausted", "lane_reused", "new_turn", "shutdown"])
def test_stale_or_busy_correction_is_not_dispatched(tmp_path: Path, guard: str):
    runtime = _runtime(tmp_path)
    prepared, _, _ = _rejected(runtime)
    sent = _capture(runtime)
    _stop(runtime)
    payload = {"operation_id": prepared.operation_id, "request_hash": prepared.request_hash,
               "workflow_run_id": prepared.workflow_run_id}
    if guard in {"settled", "cancelled"}:
        runtime.event_log.append(ZfEvent(type=f"workflow.operation.{guard}", payload=payload))
    elif guard in {"hash", "attempt", "dispatch"}:
        payload[{"hash": "request_hash", "attempt": "active_attempt_id", "dispatch": "dispatch_id"}[guard]] = "new-value"
        runtime.event_log.append(ZfEvent(type="workflow.operation.started", payload=payload))
    elif guard in {"run_complete", "run_cancel", "run_pause"}:
        runtime.event_log.append(ZfEvent(type={"run_complete": "run.goal.completed", "run_cancel": "run.cancelled", "run_pause": "run.paused"}[guard],
            correlation_id=prepared.workflow_run_id, payload={"workflow_run_id": prepared.workflow_run_id}))
    elif guard in {"pause", "exhausted"}:
        runtime.event_log.append(ZfEvent(type="dispatch.paused" if guard == "pause" else "workflow.call.result.invalid", payload=payload))
    elif guard == "lane_reused":
        runtime.event_log.append(ZfEvent(type="workflow.operation.started", payload={
            "operation_id": "other-op", "role_instance": "orchestrator", "request_hash": "other"}))
    elif guard == "new_turn":
        runtime.event_log.append(ZfEvent(type="codex.hook.user_prompt_submit", actor="orchestrator",
            payload={"session_id": "session", "turn_id": "next-turn"}))
    else:
        (runtime.state_dir / "shutdown-requested").touch()
    assert reconcile_call_result_repairs(runtime) == 0
    assert sent == []


def test_transport_failure_does_not_ack_and_can_retry(tmp_path: Path):
    runtime = _runtime(tmp_path)
    _rejected(runtime)
    _stop(runtime)
    sent = _capture(runtime)
    send = runtime._send_transport_task
    def fail(*args):
        raise RuntimeError("transport unavailable")
    runtime._send_transport_task = fail
    with pytest.raises(RuntimeError, match="transport unavailable"):
        reconcile_call_result_repairs(runtime)
    assert not any(e.type == "workflow.call.result.repair.dispatched" for e in runtime.event_log.read_all())
    runtime._send_transport_task = send
    assert reconcile_call_result_repairs(runtime) == 1
    assert len(sent) == 1


def test_orchestrator_stop_event_consumes_rejected_semantic_submit(tmp_path: Path):
    state, log, _ = _state(tmp_path)
    runtime = Orchestrator(state, _config(), RecordingTransport(), project_root=tmp_path)
    prepared, _, _ = _rejected(runtime)
    sent = _capture(runtime)
    stop = _stop(runtime)
    runtime.run_once([stop])
    assert len(sent) == 1
    assert prepared.operation_id in sent[0]
    assert any(e.type == "workflow.call.result.repair.dispatched" for e in log.read_all())


def test_required_read_instruction_never_suggests_forging_schema_evidence():
    text = repair_instruction({"field": "required_reads[0]", "code": "required_read_missing",
                               "message": "effective-config/config.json $"})
    assert "controlled artifact read" in text
    assert "effective-config/config.json $" in text
    assert "never edit required_reads" in text


def test_reader_output_correction_uses_same_claude_operation(tmp_path: Path):
    runtime, prepared, service, token = _running_workflow_read_operation(tmp_path, artifact_output=True)
    runtime.config.roles = [RoleConfig(name="scan-verification", backend="claude-code")]
    result = {"execution_status": "completed", "status": "passed", "summary": "Scope reviewed",
              "recommendation": "approve", "findings": []}
    kwargs = dict(operation_id=prepared.operation_id, role_instance="scan-verification", credential=token)
    with pytest.raises(ResultSubmitError, match="workflow_output_missing"):
        service.submit(**kwargs, semantic_result=result)
    sent = _capture(runtime)
    assert reconcile_call_result_repairs(runtime) == 0
    runtime.event_log.append(ZfEvent(type="claude.hook.stop", actor="scan-verification"))
    assert reconcile_call_result_repairs(runtime) == 1
    assert "Active task: REFACTOR-1" in sent[0]
    assert "outputs.scope" in sent[0]
    assert prepared.operation_id in sent[0]
    result["outputs"] = {"scope": {"acceptance_ids": ["AUD-AC-001"], "conclusion": "not_ready"}}
    assert service.submit(**kwargs, semantic_result=result).canonical_event_type == "workflow.child.completed"
