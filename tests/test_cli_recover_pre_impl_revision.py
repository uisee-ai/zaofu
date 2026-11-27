"""The legacy recovery entry must use the exact escalation and the live repair path."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.orchestrator_agent_pre_impl_recovery import escalated_pre_impl_submission


def legacy_history():
    ref = {"ref": "artifacts/decision.json", "sha256": "a" * 64}
    identity = {"workflow_run_id": "run-1", "operation_id": "oa-1", "source_event_id": "ready-1"}
    decision = ZfEvent(type="orchestrator.semantic.decision.submitted", actor="orchestrator",
                       correlation_id="trace", payload={**identity, "control_result_ref": ref})
    base = {**identity, "decision_event_id": decision.id, "checkpoint": "pre_impl",
            "checkpoint_policy": "blocking", "decision": "revise", "decision_ref": ref}
    def kernel(event_type, payload):
        return ZfEvent(type=event_type, origin="kernel", actor="zf-cli", causation_id=decision.id,
                       correlation_id=decision.correlation_id, payload=payload)
    return [decision, kernel("orchestrator.semantic.decision.admitted", base),
            kernel("human.escalate", {"orchestrator_operation_id": "oa-1", "orchestrator_decision_ref": ref}),
            kernel("orchestrator.semantic.decision.applied", {**base, "status": "escalated", "applied": False})]


@pytest.mark.parametrize("index,fields", [
    (2, {"origin": "worker"}), (2, {"causation_id": "unrelated"}), (2, {"correlation_id": "different"}),
    (2, {"payload": {"orchestrator_operation_id": "oa-2"}}),
    (2, {"payload": {"orchestrator_decision_ref": {"ref": "artifacts/other.json", "sha256": "a" * 64}}}),
    (1, {"origin": "external"}), (1, {"payload": {"workflow_run_id": "different"}}),
    (1, {"payload": {"checkpoint": "plan_candidate"}}), (1, {"payload": {"decision": "clarify"}}),
    (1, {"payload": {"checkpoint_policy": "shadow"}}), (3, {"payload": {"applied": True}}),
])
def test_foreign_escalation_or_outcome_cannot_select_a_plan(index, fields):
    events = legacy_history()
    if "payload" in fields:
        fields = {"payload": {**events[index].payload, **fields["payload"]}}
    events[index] = replace(events[index], **fields)
    with pytest.raises(ValueError):
        escalated_pre_impl_submission(events, events[2].id)


def test_cli_previews_and_applies_only_through_live_revision_handler(tmp_path, monkeypatch, capsys):
    from zf.runtime import orchestrator_agent_pre_impl_revision as revisions

    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text("version: '1.0'\nproject:\n  name: legacy\n  state_dir: state\n")
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("ZF_STATE_DIR", raising=False)
    state = tmp_path / "state"
    state.mkdir()
    rows = legacy_history()
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    for row in rows:
        writer.append(row)
    before = log.path.read_bytes()
    log.close()

    calls = []
    def reconcile(runtime, submitted, *, apply, escalation_event_id, approval_ref):
        calls.append((runtime.state_dir, submitted.id, apply, escalation_event_id, approval_ref))
        return {"status": "applied" if apply else "preview", "source_event_id": submitted.id}

    monkeypatch.setattr(revisions, "reconcile_pre_impl_revision", reconcile)
    command = ["recover", "pre-impl-revision", "--escalation", rows[2].id]
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "preview"
    assert (state / "events.jsonl").read_bytes() == before
    assert main([*command, "--apply"]) == 1
    assert "approval reference" in json.loads(capsys.readouterr().out)["reason"]
    monkeypatch.setattr("zf.cli.recover._run_contract_recovery_allowed", lambda *a, **kw: False)
    assert main([*command, "--apply", "--approval-ref", "owner-42"]) == 1
    assert "contract drift" in json.loads(capsys.readouterr().out)["reason"]
    monkeypatch.setattr("zf.cli.recover._run_contract_recovery_allowed", lambda *a, **kw: True)
    assert main([*command, "--apply", "--approval-ref", "owner-42"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert calls == [(state, rows[0].id, False, rows[2].id, ""),
                     (state, rows[0].id, True, rows[2].id, "owner-42")]
