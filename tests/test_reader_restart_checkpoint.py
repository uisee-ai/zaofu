"""Exact reader recovery retains immutable inputs across false Plan replans."""
import json
from types import SimpleNamespace

import pytest

from tests.test_reader_fanout_runtime import _start_fanout
from tests.test_reader_progress_recovery import _reader, _read, _close
from zf.cli.restart import _restart_role
from zf.core.events import ZfEvent
from zf.runtime.artifact_read_ledger import active_read_ledger_path
from zf.runtime.reader_restart_checkpoint import reopen_reader_restart_checkpoint
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.workflow_operation import load_workflow_operation


def failed_reader(tmp_path, *, descendant=False, legacy=False):
    state, log, transport, runtime, dispatch, manifest, ref = _reader(tmp_path, plan=True)
    # Test triggers are delivered directly; production triggers are already in the ledger.
    trigger = runtime._fanout_manifest(dispatch.payload["fanout_id"])["trigger_event_id"]
    if not any(e.id == trigger for e in log.read_all()):
        log.append(ZfEvent(id=trigger, type="candidate.ready", actor="zf-cli",
                          payload={"workflow_run_id": "trace-1"}))
    runtime.event_writer.emit("run.admission.admitted", payload={"workflow_run_id": "trace-1"})
    if legacy:
        # Reproduce the deployed producer failure that lacked its infra report,
        # so the real aggregation path mistakes absent output for Plan defects.
        append = runtime.event_writer.append
        def without_infra_report(event):
            if event.type == "fanout.child.failed":
                event.payload.pop("report", None)
            return append(event)
        runtime.event_writer.append = without_infra_report
    _read(state, manifest, ref, 0)
    _close(runtime, log, "first")
    _close(runtime, log, "second")
    failure = next(e for e in log.read_all() if e.type == "fanout.child.failed")
    latest = next(e for e in reversed(log.read_all()) if e.type == "prd.plan.failed")
    if descendant:
        new_trigger = _start_fanout(runtime, payload={"input_refs": [ref], "rework_of": latest.id})
        if not any(e.id == new_trigger.id for e in log.read_all()):
            log.append(new_trigger)
        _close(runtime, log, "derived-first")
        _close(runtime, log, "derived-second")
        latest = next(e for e in reversed(log.read_all()) if e.type == "prd.plan.failed")
    escalation = runtime.event_writer.emit("human.escalate", payload={
        "workflow_run_id": "trace-1", "source_event_id": latest.id,
        "failure_class": "stage_replan_cap_exhausted"})
    runtime.event_writer.emit("run.goal.blocked", causation_id=escalation.id,
                              payload={"workflow_run_id": "trace-1"})
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({"action": "resume-reader-checkpoint",
        "workflow_run_id": "trace-1", "failure_event_id": failure.id}))
    role = next(r for r in runtime.config.roles if r.name == "review-a")
    return state, log, transport, runtime, role, dispatch, manifest, ref, failure, str(approval)


@pytest.mark.parametrize("descendant", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_cli_restores_original_reader_request_and_read_ledger(tmp_path, monkeypatch, descendant, legacy):
    state, log, transport, runtime, role, dispatch, manifest, ref, failure, approval = failed_reader(
        tmp_path, descendant=descendant, legacy=legacy)
    before = load_workflow_operation(log, dispatch.payload["operation_id"])
    ledger = active_read_ledger_path(state, manifest["attempt_id"])
    ledger_before = ledger.read_bytes()
    alive = [True]
    monkeypatch.setattr(transport, "terminate", lambda _: alive.__setitem__(0, False), raising=False)
    monkeypatch.setattr(transport, "is_alive", lambda _: alive[0])
    monkeypatch.setattr(runtime, "_wait_role_ready", lambda _: True)
    monkeypatch.setattr("zf.cli.restart.WorkflowRuntimeCoordinator", lambda *a, **kw: runtime)
    monkeypatch.setattr(runtime, "_get_spawn_coordinator", lambda: SimpleNamespace(
        spawn=lambda *a, **kw: alive.__setitem__(0, True)))
    assert _restart_role(role.name, runtime.config, transport, log, state, tmp_path, False,
        resume_failed_reader=failure.id, recovery_approval_ref=approval) == 0
    after = load_workflow_operation(log, dispatch.payload["operation_id"])
    assert after["status"] == "running"
    for key in ("request_hash", "request_ref", "active_attempt_id", "budget_snapshot"):
        assert after[key] == before[key]
    assert ledger.read_bytes() == ledger_before
    assert transport.sent[-1][3].operation_id == before["operation_id"]
    assert build_run_admission_projection(log.read_all()).runs["trace-1"].status == "running"
    current = runtime._fanout_manifest(dispatch.payload["fanout_id"])
    assert current["children"][0]["status"] == "dispatched"
    assert not current["children"][0].get("report")
    assert current["aggregate"]["status"] == "started"
    assert not runtime._fanout_synth_dispatched(current)
    assert not runtime._fanout_identity_stale_reason(dispatch.payload["fanout_id"])[0]
    from zf.runtime.escalation_terminal import converge_unrecoverable_escalations
    assert converge_unrecoverable_escalations(log.read_all(), writer=runtime.event_writer) == 0
    # Old terminal events must not suppress new transport recovery.
    _read(state, manifest, ref, 100)
    _close(runtime, log, "after-restart")
    assert load_workflow_operation(log, before["operation_id"])["status"] == "running"
    assert transport.sent[-1][3].operation_id == before["operation_id"]


@pytest.mark.parametrize("fault", ["semantic", "policy", "admitted", "active_turn",
    "changed_generation", "unrelated_block", "approval", "corrupt_ledger", "terminal_run"])
def test_reader_checkpoint_rejects_unsafe_resume_before_writing(tmp_path, monkeypatch, fault):
    state, log, _transport, runtime, role, dispatch, manifest, _ref, failure, approval = failed_reader(tmp_path)
    if fault == "semantic":
        last = next(e for e in reversed(log.read_all()) if e.type == "fanout.synth.completed")
        runtime.event_writer.append(ZfEvent(type=last.type, actor=role.instance_id,
            payload={**last.payload, "failure_class": "plan_quality_rejected"}))
    elif fault == "policy":
        runtime.event_writer.emit("workflow.operation.blocked", payload={
            "operation_id": dispatch.payload["operation_id"], "reason": "provider_policy_refusal"})
    elif fault == "admitted":
        runtime.event_writer.emit("workflow.call.result.admitted", payload={
            "operation_id": dispatch.payload["operation_id"]})
    elif fault == "active_turn":
        monkeypatch.setattr(runtime, "_fanout_role_has_active_provider_turn", lambda _: True)
    elif fault == "changed_generation":
        _start_fanout(runtime, payload={"workflow_generation": "new"})
    elif fault == "unrelated_block":
        runtime.event_writer.emit("run.goal.blocked", causation_id="unrelated",
                                 payload={"workflow_run_id": "trace-1"})
    elif fault == "approval":
        approval = "missing.json"
    elif fault == "corrupt_ledger":
        active_read_ledger_path(state, manifest["attempt_id"]).write_text("invalid\n")
    elif fault == "terminal_run":
        runtime.event_writer.emit("run.cancelled", payload={"workflow_run_id": "trace-1"})
    count = len(log.read_all())
    with pytest.raises((ValueError, OSError)):
        reopen_reader_restart_checkpoint(runtime, role, failure.id, approval)
    assert len(log.read_all()) == count


def test_partial_reopen_is_replayable(tmp_path, monkeypatch):
    state, log, _transport, runtime, role, dispatch, _manifest, _ref, failure, approval = failed_reader(tmp_path)
    append = runtime.event_writer.append
    def interrupt(event):
        if event.type == "fanout.child.dispatched":
            raise OSError("operator process stopped")
        return append(event)
    monkeypatch.setattr(runtime.event_writer, "append", interrupt)
    with pytest.raises(OSError):
        reopen_reader_restart_checkpoint(runtime, role, failure.id, approval)
    monkeypatch.setattr(runtime.event_writer, "append", append)
    reopen_reader_restart_checkpoint(runtime, role, failure.id, approval)
    for kind in ("fanout.started", "fanout.aggregate.started", "fanout.child.dispatched"):
        assert len([e for e in log.read_all() if e.type == kind
                    and e.payload.get("recovered_failure_event_id") == failure.id]) == 1
    assert load_workflow_operation(log, dispatch.payload["operation_id"])["retry_count"] == 1


def test_reader_resume_flags_require_exact_role_and_approval(capsys):
    from zf.cli.main import main
    assert main(["restart", "--resume-failed-reader", "evt-failure"]) == 1
    assert "requires a role" in capsys.readouterr().err
