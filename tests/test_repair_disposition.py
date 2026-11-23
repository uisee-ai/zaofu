"""A controlled candidate disposition cannot erase the original recovery duty."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from tests.test_repair_revalidation import _case
from zf.core.events import ZfEvent
from zf.runtime.recovery_case import build_recovery_case_projection
from zf.runtime.repair_dispatch import RepairRequest, write_repair_contract
from zf.runtime.run_manager import build_repair_merge_queue, run_manager_tick


def _replacement(tmp_path, *, legacy=False):
    root, worktree, log, writer, config, old, failed, payload, service = _case(tmp_path, "original-failure")
    if legacy:
        writer.default_origin = ""
        requested = writer.append(ZfEvent(type="operator.intent.created", actor="operator", origin="external",
            payload={"action": "repair-closeout-validate", "action_request": dict(payload)}))
        with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
            "zf.runtime.run_manager_repair_validation.execute_repair_verification_plan",
            return_value={"ok": False, "status": "failed", "reason": "command_failed"},
        ):
            service.execute(action="repair-closeout-validate", requested_action="repair-closeout-validate",
                            payload=payload, requested=requested)
        failed = next(e for e in reversed(log.read_all()) if e.type == "run.manager.action.failed")
        assert failed.payload.get("previous_validation_event_id") == payload["previous_validation_event_id"]
        payload["previous_validation_event_id"] = failed.id
        writer.default_origin = "kernel"
    task = {"failure_identity": {"previous_queue_id": old["queue_id"], "event_id": failed.id},
            "contract": {"scope": ["src/zf/**"], "verification_commands": ["git diff --check"]}}
    accepted = writer.emit("run.manager.repair.accepted", payload={
        "candidate_id": "replacement", "fingerprint": "replacement", "repair_task_payload": task})
    contract, ref = write_repair_contract(service.state_dir, RepairRequest(
        fingerprint="replacement", attempt=0, candidate_id="replacement", candidate_path="",
        repair_task_payload=task, event_id=accepted.id), base_commit=old["base_commit"], created_by="test")
    new = {**old, "queue_id": "replacement", "candidate_id": "replacement", "fingerprint": "replacement",
           "repair_contract_ref": ref, "repair_contract_digest": ref["sha256"]}
    writer.emit("autoresearch.repair.closeout.required", payload=new)
    tested = {"source_root": str(worktree), "source_commit": new["source_commit"],
              "base_commit": new["base_commit"], "tree": "tested-tree", "changed_files": new["changed_files"]}
    writer.emit("run.manager.action.applied", payload={**new, "action": "repair-closeout-validate",
        "validation_result": {"ok": True, "tested_candidate": tested, "repair_contract_digest": ref["sha256"]}})
    merged = writer.emit("run.manager.repair.merge.merged", payload={**new, "applied_commit": "applied-replacement"})
    payload.update(replacement_queue_id="replacement", reason="Retire only the replaced candidate; preserve original failure and continuation.")
    return root, log, writer, config, old, failed, payload, service, accepted, merged


def _invoke(service, writer, payload):
    action = "repair-closeout-dispose"
    requested = writer.emit("operator.intent.created", actor="operator", payload={"action": action})
    return service.execute(action=action, requested_action=action, payload=payload, requested=requested)


def test_controlled_disposition_preserves_failure_and_replacement(tmp_path):
    _, log, writer, _, old, failed, payload, service, _, _ = _replacement(tmp_path)
    original = next(x for x in build_recovery_case_projection(log.read_all())["cases"]
                    if x["recovery_case_id"] == "original-failure")
    result = _invoke(service, writer, payload)
    assert result["ok"], result
    queue = {x["queue_id"]: x for x in build_repair_merge_queue(log.read_all())["items"]}
    assert queue[old["queue_id"]]["status"] == "discarded"
    assert queue[old["queue_id"]]["validation"]["event_id"] == failed.id
    assert queue["replacement"]["status"] == "merged"
    assert next(x for x in build_recovery_case_projection(log.read_all())["cases"]
                if x["recovery_case_id"] == "original-failure") == original
    assert not any(e.type in {"task.done", "run.completed", "workflow.resume.requested"} for e in log.read_all())
    assert not _invoke(service, writer, payload)["ok"]
    from tests.test_repair_revalidation import _invoke as revalidate
    assert not revalidate(service, writer, payload)["ok"]
    assert len([e for e in log.read_all() if e.type == "run.manager.repair.merge.discarded"]) == 1


@pytest.mark.parametrize("fault", ["failure", "digest", "replacement", "disabled", "paused", "human", "reason",
                                   "unapplied", "unlinked", "untrusted_apply", "untrusted_validation"])
def test_disposition_rejects_stale_or_unbound_evidence(tmp_path, fault):
    _, log, writer, config, old, _, payload, service, accepted, merged = _replacement(tmp_path)
    if fault == "failure": payload["previous_validation_event_id"] = "stale"
    elif fault == "digest": payload["repair_contract_digest"] = "stale"
    elif fault == "replacement": payload["replacement_queue_id"] = old["queue_id"]
    elif fault == "disabled": config.runtime.run_manager.source_repair.enabled = False
    elif fault == "paused": writer.emit("dispatch.paused", actor="operator")
    elif fault == "human": writer.emit("human.escalate", payload={"decision_token": "owner"})
    elif fault == "reason": payload["reason"] = ""
    else:
        events = deepcopy(log.read_all())
        if fault == "unapplied": events = [e for e in events if e.id != merged.id]
        elif fault == "unlinked": next(e for e in events if e.id == accepted.id).payload["repair_task_payload"]["failure_identity"]["event_id"] = "foreign"
        elif fault == "untrusted_apply": next(e for e in events if e.id == merged.id).origin = "worker"
        else:
            next(e for e in events if e.type == "run.manager.action.applied").origin = "worker"
        with patch.object(log, "read_all", return_value=events):
            assert not _invoke(service, writer, payload)["ok"]
        return
    assert not _invoke(service, writer, payload)["ok"]
    assert not any(e.type == "run.manager.repair.merge.discarded" for e in log.read_all())


def test_resident_disposition_uses_current_bound_case_and_runtime_entry(tmp_path):
    _, log, writer, config, old, _, payload, service, _, _ = _replacement(tmp_path)
    writer.emit("run.manager.resident.prompted", payload={"request_id": "disposition-1", "instance_id": "run-manager"})
    writer.emit("run.manager.action.effect.pending", payload={"request_id": "disposition-1",
        "action": "resident-agent-reprompt", "recovery_case_id": "diagnosis",
        "continuation_expected_events": ["run.manager.agent.recommendation"]})
    writer.emit("run.manager.agent.recommendation", actor="run-manager", payload={**payload,
        "request_id": "disposition-1", "recommended_route": "controlled_action", "action": "repair-closeout-dispose"})
    prior = next(x for x in build_recovery_case_projection(log.read_all())["cases"]
                 if x["recovery_case_id"] == "diagnosis")
    run_manager_tick(state_dir=service.state_dir, writer=writer, config=config, project_root=tmp_path, spawn_repairs=False)
    row = next(x for x in build_repair_merge_queue(log.read_all())["items"] if x["queue_id"] == old["queue_id"])
    assert row["status"] == "discarded"
    assert next(x for x in build_recovery_case_projection(log.read_all())["cases"]
                if x["recovery_case_id"] == "diagnosis") == prior
    assert all(e.payload.get("recovery_case_id", "").startswith("repair-disposition-")
               for e in log.read_all() if e.payload.get("action") == "repair-closeout-dispose"
               and e.type.startswith("run.manager.action."))


@pytest.mark.parametrize("mutation", ["worker", "queue_id", "source_commit", "repair_contract_digest", "previous_validation_event_id"])
def test_disposition_receipt_must_bind_current_strict_candidate(tmp_path, mutation):
    _, log, writer, _, old, _, payload, service, _, _ = _replacement(tmp_path)
    assert _invoke(service, writer, payload)["ok"]
    es = log.read_all(); index = next(i for i,e in enumerate(es) if e.type == "run.manager.repair.merge.discarded")
    receipt = deepcopy(es[index]); prefix = es[:index]
    if mutation == "worker": receipt.origin = "worker"
    else: receipt.payload[mutation] = "foreign"
    row = next(x for x in build_repair_merge_queue([*prefix, receipt])["items"] if x["queue_id"] == old["queue_id"])
    assert row["status"] == "needs_review"


@pytest.mark.parametrize("broken", ["", "queue", "previous_failure", "source_commit"])
def test_legacy_operator_failure_needs_complete_bound_causation(tmp_path, broken):
    from zf.runtime.repair_disposition import prepare_disposition

    _, log, _, config, _, failed, payload, service, _, _ = _replacement(tmp_path, legacy=True)
    events = deepcopy(log.read_all())
    failure = next(e for e in events if e.id == failed.id)
    planned = next(e for e in events if e.id == failure.causation_id)
    intent = next(e for e in events if e.id == planned.causation_id)
    if broken == "queue": intent.payload["action_request"]["queue_id"] = "foreign"
    if broken == "previous_failure": intent.payload["action_request"]["previous_validation_event_id"] = "foreign"
    if broken == "source_commit": planned.payload["source_commit"] = "foreign"
    if broken:
        with pytest.raises(ValueError, match="failure_not_trusted"):
            prepare_disposition(state_dir=service.state_dir, events=events, config=config,
                                payload=payload, request_event_id="preview")
    else:
        assert prepare_disposition(state_dir=service.state_dir, events=events, config=config,
                                   payload=payload, request_event_id="preview")["queue_id"] == payload["queue_id"]


@pytest.mark.parametrize("blocked_case", ["request", "disposition"])
def test_resident_cannot_bypass_either_case_gate(tmp_path, blocked_case):
    from zf.runtime.repair_disposition import prepare_disposition

    _, log, writer, config, _, _, payload, service, _, _ = _replacement(tmp_path)
    prepared = prepare_disposition(state_dir=service.state_dir, events=log.read_all(), config=config,
                                   payload=payload, request_event_id="preview")
    writer.emit("run.manager.resident.prompted", payload={"request_id": "gated", "instance_id": "run-manager"})
    writer.emit("run.manager.action.effect.pending", payload={"request_id": "gated",
        "action": "resident-agent-reprompt", "recovery_case_id": "request-case",
        "continuation_expected_events": ["run.manager.agent.recommendation"]})
    writer.emit("run.manager.action.blocked", payload={
        "recovery_case_id": "request-case" if blocked_case == "request" else prepared["recovery_case_id"]})
    writer.emit("run.manager.agent.recommendation", actor="run-manager", payload={**payload,
        "request_id": "gated", "recommended_route": "controlled_action", "action": "repair-closeout-dispose"})
    run_manager_tick(state_dir=service.state_dir, writer=writer, config=config, project_root=tmp_path, spawn_repairs=False)
    assert not any(e.type == "run.manager.repair.merge.discarded" for e in log.read_all())
