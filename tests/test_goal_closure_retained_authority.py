"""Thin Judge rechecks the same frozen Task proof accepted by Candidate Verify."""
import json
from types import SimpleNamespace

import pytest

from tests.test_candidate_retained_reviewed_proofs import retained, prepare
from tests.test_plan_completion_reuse import recovery
from zf.runtime.call_result_authority import CallResultAuthorityMixin
from zf.runtime.candidate_currentness import candidate_currentness_issues


def closure(r):
    fields = prepare(r)
    assert not candidate_currentness_issues(r.runtime.state_dir, r.runtime.event_log.read_all(),
        identity={"workflow_run_id": r.source.correlation_id, **fields},
        contract_ref=fields["contract_snapshot_ref"], contract_digest=fields["contract_snapshot_digest"])
    contract = json.loads((r.runtime.state_dir / fields["contract_snapshot_ref"]).read_text())
    snapshot = {key: contract[key] for key in ("workflow_run_id", "task_map_generation",
        "plan_artifact_package_id", "plan_artifact_package_ref", "plan_artifact_package_digest", "child_authorities")}
    snapshot.update(schema_version="goal-closure-contract-snapshot.v1", task_authority_required=True)
    ref = r.sidecar(snapshot)
    fact = r.sidecar({"candidate_verify_contract_ref": fields["contract_snapshot_ref"]})
    result = {**fields, "workflow_run_id": r.source.correlation_id,
        "closure_fact_ref": fact["ref"], "closure_fact_digest": fact["sha256"],
        "contract_snapshot_ref": ref["ref"], "contract_snapshot_digest": ref["sha256"]}
    envelope = r.sidecar({"schema_version": "call-result-envelope.v1",
        "identity": {"workflow_run_id": r.source.correlation_id, **fields,
            "operation_id": "candidate-verify", "request_hash": "candidate-request",
            "attempt_id": "candidate-attempt", "producer_role": "verify"},
        "execution": {"status": "completed"},
        "control_result": r.sidecar({"schema_version": "verification-result.v1", "verdict": "passed"})})
    r.emit("workflow.call.result.admitted", {"control_result_schema": "verification-result.v1",
        "semantic_verdict": "passed", "envelope_ref": envelope})
    r.emit("flow.goal.closed", {**result, "input_result_refs": [envelope["ref"]]})
    return result, snapshot


def issues(r, result):
    reader = SimpleNamespace(state_dir=r.runtime.state_dir, event_log=r.runtime.event_log)
    return CallResultAuthorityMixin._goal_closure_task_authority_issues(reader, result)


def test_thin_judge_accepts_exact_retained_candidate_authorities(retained):
    result, snapshot = closure(retained)
    original = json.dumps(snapshot, sort_keys=True)
    assert not issues(retained, result)
    assert json.dumps(snapshot, sort_keys=True) == original
    # The semantic output cannot replace the kernel-owned frozen child set.
    result["child_authorities"] = [{"task_id": "invented"}]
    assert not issues(retained, result)


def test_new_closure_with_same_children_binds_its_new_verified_freeze(retained):
    r = retained
    old, _ = closure(r)
    r.emit("candidate.ready", dict(r.current.payload))
    new, _ = closure(r)
    assert old["contract_snapshot_ref"] == new["contract_snapshot_ref"]
    assert old["closure_fact_ref"] != new["closure_fact_ref"]
    assert not issues(r, new)
    assert issues(r, old)


@pytest.mark.parametrize("fault", ["proof", "current_contract", "ordinary_contract", "candidate", "native_id", "target", "generation", "snapshot_proof"])
def test_thin_judge_still_rejects_changed_retained_authority(retained, fault):
    r = retained
    result, snapshot = closure(r)
    child = next(row for row in snapshot["child_authorities"] if row["task_id"] == "TASK-1")
    if fault == "proof":
        (r.runtime.state_dir / child["completion_reuse_ref"]["ref"]).write_text("{}")
    elif fault in {"current_contract", "ordinary_contract"}:
        from copy import deepcopy
        from zf.runtime.task_contract_authority import TaskContractAuthorityService

        task = r.runtime.task_store.get("TASK-1" if fault == "current_contract" else "TASK-2")
        contract = deepcopy(task.contract)
        contract.scope = ["changed.py"]
        TaskContractAuthorityService(task_store=r.runtime.task_store,
            event_writer=r.runtime.event_writer, state_dir=r.runtime.state_dir).replace(
                task, contract=contract, source="test-contract-change", reopen_terminal=True,
                task_updates={"status": "backlog"})
        r.runtime.task_store.update(task.id, status="done")
    elif fault == "candidate":
        body = {**r.current.payload, "candidate_head_commit": "f" * 40}
        ref = r.sidecar({key: value for key, value in body.items()
                         if key not in {"freeze_receipt_ref", "freeze_receipt_digest"}})
        r.emit("candidate.ready", {**body, "freeze_receipt_ref": ref, "freeze_receipt_digest": ref["sha256"]})
    elif fault == "native_id":
        r.emit("candidate.ready", dict(r.current.payload))
    elif fault == "target":
        result["target_commit"] = "f" * 40
    elif fault == "generation":
        snapshot["task_map_generation"] = "foreign-generation"
    else:
        child["verification_event_id"] = "foreign-verify"
    if fault in {"generation", "snapshot_proof"}:
        ref = r.sidecar(snapshot)
        result.update(contract_snapshot_ref=ref["ref"], contract_snapshot_digest=ref["sha256"])
    result["child_authorities"] = []
    assert issues(r, result)
