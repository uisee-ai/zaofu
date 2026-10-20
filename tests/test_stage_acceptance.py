from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.impl_self_check import ImplSelfCheckError, completion_payload_template, normalize_impl_self_check
from zf.runtime.verification_result import VerificationResultError, normalize_verification_result
from tests.test_impl_self_check import _payload, _snapshots
from tests.test_writer_contract_handoff import _impl_self_check_body, _replace_contract, _typed_contract
from tests.test_writer_fanout_runtime import _child, _commit, _fanout_id, _manifest, _seed_tasks, _start, _state


def _future_criteria():
    return [{"acceptance_id": name, "statement": f"{owner} outcome", "mandatory": True,
             "verification_owner": owner, "verification_tier": "integration", "verification_command_ids": []}
            for name, owner in [("AC-CANDIDATE", "candidate_verify"), ("AC-HUMAN", "human")]]


def _pending_results():
    return [{"acceptance_id": item["acceptance_id"], "status": "blocked", "evidence_refs": [],
             "command_receipt_ids": [], "residual_risks": ["Pending later verification stage"]}
            for item in _future_criteria()]


def test_local_self_check_keeps_future_mandatory_acceptance_pending(tmp_path):
    contract, target = _snapshots(tmp_path)
    contract["acceptance_criteria"].extend(_future_criteria())
    payload = _payload(contract, target)
    payload["impl_self_check"]["acceptance_results"].extend(_pending_results())
    body = normalize_impl_self_check(payload, contract_snapshot=contract, target_snapshot=target)
    assert [row["status"] for row in body["acceptance_results"]] == ["passed", "blocked", "blocked"]
    assert all(row["mandatory"] for row in contract["acceptance_criteria"])


@pytest.mark.parametrize("local_status", ["failed", "blocked", "waived"])
def test_future_owner_cannot_excuse_local_failure(tmp_path, local_status):
    contract, target = _snapshots(tmp_path)
    contract["acceptance_criteria"].extend(_future_criteria())
    payload = _payload(contract, target)
    payload["impl_self_check"]["acceptance_results"][0]["status"] = local_status
    payload["impl_self_check"]["acceptance_results"].extend(_pending_results())
    with pytest.raises(ImplSelfCheckError, match="AC-1"):
        normalize_impl_self_check(payload, contract_snapshot=contract, target_snapshot=target)


def test_template_does_not_predeclare_future_success(tmp_path):
    contract, _ = _snapshots(tmp_path)
    contract["acceptance_criteria"].extend(_future_criteria())
    template = completion_payload_template(contract_snapshot=contract, task_item={}, task_id=contract["task_id"], run_id="run", child_id="child")
    assert [r["status"] for r in template["impl_self_check"]["acceptance_results"]] == ["passed", "blocked", "blocked"]


@pytest.mark.parametrize("future_status", ["passed", "waived"])
def test_implementation_cannot_predeclare_later_acceptance(tmp_path, future_status):
    contract, target = _snapshots(tmp_path)
    contract["acceptance_criteria"].extend(_future_criteria())
    payload = _payload(contract, target)
    pending = _pending_results()
    pending[0].update({"status": future_status, "evidence_refs": ["artifacts/claimed.log"]})
    payload["impl_self_check"]["acceptance_results"].extend(pending)
    with pytest.raises(ImplSelfCheckError, match="later verification"):
        normalize_impl_self_check(payload, contract_snapshot=contract, target_snapshot=target)


def _verify_payload():
    return {"verification_result": {
        "execution_status": "completed", "verdict": "passed", "summary": "Local behavior verified",
        "evidence_refs": ["artifacts/local.log"], "reused_command_receipt_ids": ["receipt-unit"],
        "requirement_results": [{"acceptance_id": "AC-1", "status": "passed", "evidence_refs": ["artifacts/local.log"]}],
    }}


def test_task_verify_can_finish_without_claiming_candidate_or_human_result(tmp_path):
    contract, target = _snapshots(tmp_path)
    contract["acceptance_criteria"].extend(_future_criteria())
    result = normalize_verification_result(_verify_payload(), contract_snapshot=contract, target_snapshot=target)
    assert [r["acceptance_id"] for r in result["requirement_results"]] == ["AC-1"]
    with pytest.raises(VerificationResultError, match="AC-CANDIDATE"):
        normalize_verification_result(_verify_payload(), contract_snapshot=contract, target_snapshot=target, default_owner="candidate_verify")


def test_worker_cannot_change_acceptance_owner_in_result(tmp_path):
    contract, target = _snapshots(tmp_path)
    payload = _verify_payload()
    payload["verification_result"]["requirement_results"][0].update({"status": "blocked", "verification_owner": "candidate_verify"})
    with pytest.raises(VerificationResultError, match="owner mismatch"):
        normalize_verification_result(payload, contract_snapshot=contract, target_snapshot=target)


@pytest.mark.parametrize("status", ["not_applicable", "waived"])
def test_candidate_mandatory_acceptance_cannot_be_waived_by_stage_routing(tmp_path, status):
    contract, target = _snapshots(tmp_path)
    contract["acceptance_criteria"].extend(_future_criteria())
    payload = _verify_payload()
    payload["verification_result"]["requirement_results"].append({
        "acceptance_id": "AC-CANDIDATE", "status": status,
    })
    with pytest.raises(VerificationResultError, match="mandatory acceptance did not pass"):
        normalize_verification_result(payload, contract_snapshot=contract, target_snapshot=target, default_owner="candidate_verify")


def test_real_writer_handoff_accepts_local_proof_and_retains_pending_candidate(tmp_path):
    state, log, _, orch = _state(tmp_path, harness_profile="baseline")
    orch._typed_task_contract_handoff_enabled = lambda _: True
    orch.config.workflow.impl_self_check_required = True
    _seed_tasks(state)
    contract = _typed_contract()
    contract.acceptance_criteria.extend(_future_criteria())
    _replace_contract(state, TaskStore(state / "kanban.json"), contract)
    _start(orch)
    fanout = _fanout_id(log)
    child = _child(_manifest(state, fanout), "TASK-1")
    snapshot = json.loads((state / child["contract_snapshot_ref"]).read_text())
    snapshot.update({"contract_snapshot_ref": child["contract_snapshot_ref"], "contract_snapshot_digest": child["contract_snapshot_digest"]})
    commit = _commit(Path(child["workdir"]), "a.txt", "delivered\n", "deliver local behavior")
    attempt = str(child.get("attempt_id") or child["run_id"])
    check = _impl_self_check_body(snapshot, attempt_id=attempt, source_commit=commit)
    check["acceptance_results"].extend(_pending_results())
    event = ZfEvent(type="dev.build.done", actor=child["role_instance"], task_id="TASK-1", correlation_id="trace-1", payload={
        "fanout_id": fanout, "child_id": child["child_id"], "run_id": child["run_id"],
        "dispatch_id": child["run_id"], "attempt_id": attempt, "source_branch": child["source_branch"],
        "workdir": child["workdir"], "source_commit": commit, "workflow_run_id": "trace-1",
        "contract_revision": snapshot["contract_revision"], "task_map_generation": snapshot["task_map_generation"],
        "contract_snapshot_ref": child["contract_snapshot_ref"], "contract_snapshot_digest": child["contract_snapshot_digest"],
        "impl_self_check": check,
    })
    log.append(event)
    orch._maybe_update_writer_fanout(event)
    checks = [e for e in log.read_all() if e.type == "impl.self_check.completed" and e.causation_id == event.id]
    assert len(checks) == 1
    stored = json.loads((state / checks[0].payload["impl_self_check_ref"]).read_text())
    assert [r["status"] for r in stored["acceptance_results"]] == ["passed", "blocked", "blocked"]
    assert not [e for e in log.read_all() if e.type == "fanout.child.failed" and e.causation_id == event.id]
    ref_updated = ZfEvent(type="task.ref.updated", actor="zf-cli", task_id="TASK-1", correlation_id="trace-1", causation_id=event.id, payload={
        "task_id": "TASK-1", "task_ref": "task/TASK-1", "trigger_event_id": event.id,
        "source_branch": child["source_branch"], "source_commit": commit,
    })
    log.append(ref_updated)
    orch._maybe_update_writer_fanout(ref_updated)
    orch._maybe_update_writer_fanout(ref_updated)
    completed = [e for e in log.read_all() if e.type == "fanout.child.completed" and e.payload.get("task_id") == "TASK-1"]
    assert len(completed) == 1
