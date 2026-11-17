"""Approved timeout recovery must continue the exact review, including reads."""
import json
from types import SimpleNamespace

import pytest

from tests.test_reader_respawn_continuation import _running_recovered_synth
from zf.cli.restart import _restart_role
from zf.core.events import ZfEvent
from zf.runtime.artifact_read_ledger import active_read_ledger_path, read_attempt_artifact
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.synth_restart_checkpoint import reopen_synth_restart_checkpoint
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


def timeout_checkpoint(tmp_path, *, suspended=False):
    state, log, runtime, role, dispatch, before = _running_recovered_synth(tmp_path)
    runtime.event_writer.emit("run.admission.admitted", payload={"workflow_run_id": "trace-product"})
    failure = runtime.event_writer.append(ZfEvent(type="fanout.synth.completed", actor="zf-cli", origin="kernel",
        payload={**{k: dispatch.payload[k] for k in ("fanout_id", "run_id", "stage_id", "role_instance")},
                 "status": "failed", "reason": "synth_timeout"}))
    runtime.event_writer.append(ZfEvent(type="fanout.aggregate.completed", actor="zf-cli", origin="kernel",
        payload={"fanout_id": dispatch.payload["fanout_id"], "status": "failed",
                 "failure_event": "task_map.blocked", "synth_event_id": failure.id}))
    stage_failure = runtime.event_writer.append(ZfEvent(type="task_map.blocked", actor="zf-cli",
        origin="kernel", causation_id=failure.id, payload={
            "fanout_id": failure.payload["fanout_id"], "workflow_run_id": "trace-product"}))
    escalation = runtime.event_writer.append(ZfEvent(type="human.escalate", actor="orchestrator",
        payload={"source_event_id": stage_failure.id, "failure_class": "stage_replan_cap_exhausted"}))
    runtime.event_writer.append(ZfEvent(type="run.goal.blocked", actor="run-manager",
        causation_id=escalation.id, payload={"workflow_run_id": "trace-product", "status": "blocked"}))
    manifest = hydrate_sidecar_ref(state, dispatch.payload["attempt_source_manifest"]).payload
    source = next(s for s in manifest["sources"] if s["source_id"] == "plan-synth-contract")
    read_attempt_artifact(state, manifest=manifest, source_id=source["source_id"],
                          artifact_id=source["artifact_id"], page_start=0, page_size=100)
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({"action": "resume-synth-checkpoint",
        "failure_event_id": failure.id, "workflow_run_id": "trace-product"}))
    if suspended:
        WorkflowOperationService(state_dir=state, event_log=log, event_writer=runtime.event_writer).interrupt(
            operation_id=before["operation_id"], request_hash=before["request_hash"],
            workflow_run_id="trace-product", task_id="", reason="graceful_stop")
    return state, log, runtime, role, dispatch, before, failure, approval


@pytest.mark.parametrize("suspended", [False, True])
def test_cli_reopens_approved_timeout_on_same_request_and_read_ledger(tmp_path, monkeypatch, suspended):
    state, log, runtime, role, dispatch, before, failure, approval = timeout_checkpoint(
        tmp_path, suspended=suspended)
    ledger = active_read_ledger_path(state, before["active_attempt_id"])
    read_bytes = ledger.read_bytes()
    alive = [True]
    monkeypatch.setattr(runtime.transport, "terminate", lambda _: alive.__setitem__(0, False), raising=False)
    monkeypatch.setattr(runtime.transport, "is_alive", lambda _: alive[0])
    monkeypatch.setattr(runtime, "_wait_role_ready", lambda _: True)
    monkeypatch.setattr("zf.cli.restart.WorkflowRuntimeCoordinator", lambda *a, **kw: runtime)
    monkeypatch.setattr(runtime, "_get_spawn_coordinator", lambda: SimpleNamespace(
        spawn=lambda *a, **kw: alive.__setitem__(0, True)))
    assert _restart_role(role.name, runtime.config, runtime.transport, log, state, tmp_path, False,
                         resume_failed_synth=failure.id, recovery_approval_ref=str(approval)) == 0
    after = reduce_workflow_operations(log.read_all())[before["operation_id"]]
    assert after["status"] == "running"
    for key in ("request_hash", "request_ref", "active_attempt_id", "budget_snapshot"):
        assert after[key] == before[key]
    assert ledger.read_bytes() == read_bytes
    assert build_run_admission_projection(log.read_all()).runs["trace-product"].status == "running"
    assert runtime.transport.sent[-1][3].operation_id == before["operation_id"]
    assert "required" in runtime.transport.sent[-1][2]
    assert any(e.id == failure.id for e in log.read_all())


@pytest.mark.parametrize("fault", ["no_approval", "wrong_approval", "unrelated_block", "newer_stage",
    "semantic", "admitted", "policy", "active_turn", "no_reads", "tampered_request"])
def test_timeout_resume_rejects_unsafe_checkpoint_without_writes(tmp_path, fault):
    state, log, runtime, role, dispatch, before, failure, approval = timeout_checkpoint(tmp_path)
    if fault == "wrong_approval":
        approval.write_text('{}')
    elif fault == "unrelated_block":
        runtime.event_writer.emit("run.goal.blocked", payload={"workflow_run_id": "trace-product"})
    elif fault == "newer_stage":
        runtime.event_writer.emit("fanout.started", payload={"fanout_id": "newer", "trace_id": "trace-product",
                                                           "stage_id": dispatch.payload["stage_id"]})
    elif fault == "semantic":
        failure = runtime.event_writer.append(ZfEvent(type=failure.type, actor="zf-cli", origin="kernel",
            payload={**failure.payload, "report": {"recommendation": "reject"}}))
    elif fault == "admitted":
        runtime.event_writer.emit("workflow.call.result.admitted", payload={
            "operation_id": before["operation_id"], "request_hash": before["request_hash"]})
    elif fault == "policy":
        runtime.event_writer.emit("workflow.operation.blocked", payload={
            "operation_id": before["operation_id"], "reason": "provider_policy_refusal"})
    elif fault == "active_turn":
        role.backend = "codex"
        log.append(ZfEvent(type="codex.hook.user_prompt_submit", actor=role.instance_id,
                          payload={"session_id": "s", "turn_id": "active"}))
    elif fault == "no_reads":
        active_read_ledger_path(state, before["active_attempt_id"]).unlink()
    elif fault == "tampered_request":
        (state / before["request_ref"]["ref"]).write_text('{}')
    count = len(log.read_all())
    with pytest.raises(ValueError):
        reopen_synth_restart_checkpoint(runtime, role, failure.id,
                                       approval_ref="" if fault == "no_approval" else str(approval))
    assert len(log.read_all()) == count


@pytest.mark.parametrize("interrupted_type", ["workflow.operation.redrive_admitted", "run.goal.updated",
                                             "fanout.synth.dispatched"])
def test_timeout_reopen_replays_after_goal_update_without_duplicate_recovery(tmp_path, monkeypatch, interrupted_type):
    _state, log, runtime, role, _dispatch, _before, failure, approval = timeout_checkpoint(tmp_path, suspended=True)
    append = runtime.event_writer.append
    def interrupted(event):
        if event.type == interrupted_type:
            raise OSError("operator interrupted")
        return append(event)
    monkeypatch.setattr(runtime.event_writer, "append", interrupted)
    with pytest.raises(OSError):
        reopen_synth_restart_checkpoint(runtime, role, failure.id, approval_ref=str(approval))
    monkeypatch.setattr(runtime.event_writer, "append", append)
    reopen_synth_restart_checkpoint(runtime, role, failure.id, approval_ref=str(approval))
    for kind in ("run.goal.updated", "workflow.operation.redrive_admitted"):
        assert len([e for e in log.read_all() if e.type == kind]) == 1
    assert len([e for e in log.read_all() if e.type == "fanout.aggregate.started"
                and e.payload.get("recovered_failure_event_id") == failure.id]) == 1
