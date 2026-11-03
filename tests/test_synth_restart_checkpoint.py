"""Operator restart resumes a failed transport without replacing the review."""
from types import SimpleNamespace

import pytest

from tests.test_reader_respawn_continuation import _running_recovered_synth
from zf.cli.restart import _restart_role
from zf.core.events import ZfEvent
from zf.runtime.synth_restart_checkpoint import reopen_synth_restart_checkpoint
from zf.runtime.workflow_operation import reduce_workflow_operations


def failed_synth(tmp_path):
    state, log, runtime, role, dispatch, before = _running_recovered_synth(tmp_path)
    runtime.event_writer.append(ZfEvent(type="run.admission.admitted", actor="zf-cli",
                                       payload={"workflow_run_id": "trace-product"}))
    log.append(ZfEvent(type="provider.turn.closed", actor=role.instance_id,
                       payload={"backend": "codex", "turn_id": "quota-stop"}))
    assert runtime._recover_lost_fanout_synth_dispatches(log.read_all())
    failure = next(e for e in log.read_all() if e.type == "fanout.synth.completed")
    return state, log, runtime, role, dispatch, before, failure


def test_cli_resumes_exact_failed_synth_without_replacing_request(tmp_path, monkeypatch):
    state, log, runtime, role, dispatch, before, failure = failed_synth(tmp_path)
    alive = [True]
    monkeypatch.setattr(runtime.transport, "terminate", lambda _: alive.__setitem__(0, False), raising=False)
    monkeypatch.setattr(runtime.transport, "is_alive", lambda _: alive[0])
    monkeypatch.setattr(runtime, "_wait_role_ready", lambda _: True)
    monkeypatch.setattr("zf.cli.restart.WorkflowRuntimeCoordinator", lambda *a, **kw: runtime)
    monkeypatch.setattr(runtime, "_get_spawn_coordinator", lambda: SimpleNamespace(
        spawn=lambda *a, **kw: alive.__setitem__(0, True)))
    assert _restart_role(role.name, runtime.config, runtime.transport, log, state, tmp_path,
                         False, resume_failed_synth=failure.id) == 0
    after = reduce_workflow_operations(log.read_all())[dispatch.payload["operation_id"]]
    for key in ("request_hash", "request_ref", "active_attempt_id", "budget_snapshot"):
        assert after[key] == before[key]
    manifest = runtime._fanout_manifest(dispatch.payload["fanout_id"])
    assert manifest["synth"]["status"] == "dispatched"
    assert manifest["aggregate"]["status"] == "started"
    assert runtime.transport.sent[-1][3].operation_id == dispatch.payload["operation_id"]
    assert len([e for e in log.read_all() if e.type == "fanout.synth.completed"]) == 1
    assert any(e.type == "worker.recovery.injected" for e in log.read_all())
    with pytest.raises(ValueError, match="newer dispatch"):
        reopen_synth_restart_checkpoint(runtime, role, failure.id)


@pytest.mark.parametrize("fault", ["semantic", "refusal", "terminal_run", "wrong_role", "missing", "tampered_request", "admitted", "active_turn", "missing_briefing"])
def test_failed_synth_resume_rejects_unsafe_checkpoint(tmp_path, fault):
    state, log, runtime, role, dispatch, before, failure = failed_synth(tmp_path)
    if fault == "semantic":
        failure = runtime.event_writer.append(ZfEvent(type="fanout.synth.completed",
            actor=role.instance_id, payload={**failure.payload, "report": {"recommendation": "reject"}}))
    elif fault == "refusal":
        runtime.event_writer.emit("workflow.operation.blocked", payload={
            "operation_id": dispatch.payload["operation_id"], "reason": "provider_policy_refusal"})
    elif fault == "terminal_run":
        runtime.event_writer.emit("run.cancelled", payload={"workflow_run_id": "trace-product"})
    elif fault == "wrong_role": role = SimpleNamespace(instance_id="another-reader")
    elif fault == "missing": failure = SimpleNamespace(id="missing")
    elif fault == "tampered_request":
        (state / before["request_ref"]["ref"]).write_text('{}')
    elif fault == "admitted":
        runtime.event_writer.emit("workflow.call.result.admitted", payload={
            "operation_id": dispatch.payload["operation_id"], "request_hash": before["request_hash"]})
    elif fault == "active_turn":
        role.backend = "codex"
        log.append(ZfEvent(type="codex.hook.user_prompt_submit", actor=role.instance_id,
                          payload={"session_id": "session-1", "turn_id": "still-working"}))
    elif fault == "missing_briefing":
        from pathlib import Path
        Path(dispatch.payload["briefing_path"]).unlink()
    original = len(log.read_all())
    with pytest.raises(ValueError):
        reopen_synth_restart_checkpoint(runtime, role, failure.id)
    assert len(log.read_all()) == original


def test_partial_reopen_replays_without_duplicate_aggregate(tmp_path, monkeypatch):
    _state, log, runtime, role, _dispatch, _before, failure = failed_synth(tmp_path)
    append = runtime.event_writer.append
    def interrupted(event):
        if event.type == "fanout.synth.dispatched": raise OSError("operator process stopped")
        return append(event)
    monkeypatch.setattr(runtime.event_writer, "append", interrupted)
    with pytest.raises(OSError):
        reopen_synth_restart_checkpoint(runtime, role, failure.id)
    monkeypatch.setattr(runtime.event_writer, "append", append)
    reopen_synth_restart_checkpoint(runtime, role, failure.id)
    reopened = [e for e in log.read_all() if e.type == "fanout.aggregate.started"
                and e.payload.get("recovered_failure_event_id") == failure.id]
    assert len(reopened) == 1


def test_resume_flag_cannot_apply_to_full_restart(capsys):
    from zf.cli.main import main
    assert main(["restart", "--resume-failed-synth", "evt-failure"]) == 1
    assert "requires a role" in capsys.readouterr().err
