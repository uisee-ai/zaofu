"""Repeated reviewed Plan recovery retains original native verification."""
from copy import deepcopy
import json

import pytest

from tests.test_plan_completion_reuse import recovery, action, reviewed
from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
from zf.runtime.run_contract import write_run_contract_snapshot
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts


def next_revision(r, generation="generation-3"):
    assert action(r, reviewed_digest=reviewed(r), approval_ref="owner:second",
                  reuse_rationale="same Task acceptance")["ok"]
    freeze = json.loads((r.runtime.state_dir / r.candidate.payload["freeze_receipt_ref"]["ref"]).read_text())
    freeze["task_map_generation"] = r.source.payload["task_map_generation"]
    freeze_ref = r.sidecar(freeze)
    r.emit("candidate.ready", {**r.candidate.payload, **r.source.payload,
                              "freeze_receipt_ref": freeze_ref, "freeze_receipt_digest": freeze_ref["sha256"]})
    state = r.runtime.state_dir
    prior_map = json.loads((state / r.source.payload["task_map_ref"]).read_text())
    task_map = r.sidecar({"tasks": prior_map["tasks"], "target_commit": "c" * 40})
    pkg = build_plan_artifact_package(workflow_run_id=r.source.correlation_id, flow_kind="issue",
        producer_stage_id="plan", run_contract=write_run_contract_snapshot(state, {"schema_version": "run-contract.v1"}),
        plan_revision=generation, task_map_generation=generation,
        produced=[{**task_map, "logical_name": "task_map"}], required_ports=["task_map"])
    ref = write_plan_artifact_package(state, pkg)
    r.emit("plan.artifact_package.admitted", {"package_ref": ref["ref"], "package_digest": ref["sha256"]})
    bindings = {"task_map_ref": task_map["ref"], "task_map_generation": generation,
                "plan_artifact_package_ref": ref["ref"], "plan_artifact_package_digest": ref["sha256"]}
    r.source = r.emit("task_map.ready", bindings)
    r.emit("plan.approved", {"plan_id": r.source.id})
    r.emit("task.pipeline.generation.admitted", {**bindings, "schema_version": "task-pipeline-generation.v1",
        "generation_id": generation, "trigger_event_id": r.source.id, "task_ids": ["TASK-1"], "dispatch_base_commit": "c" * 40})
    task = deepcopy(r.runtime.task_store.get("TASK-1"))
    task.status = "backlog"
    task.completed_at = None
    task.contract_authority_revision = generation + "-authority"
    task.contract.evidence_contract["source_refs"] = bindings
    r.runtime.task_store.reopen(task)


def test_third_plan_uses_original_native_completion_and_fresh_candidate_authority(recovery):
    r = recovery
    next_revision(r)
    result = action(r, dry_run=True)
    assert result["ok"], result
    row = result["manifest"]["tasks"][0]
    assert row["completion_event_id"] == r.done.id
    assert row["verification_event_id"] == r.verify.id
    assert row["evidence_task_map_generation"] == "generation-1"
    assert row["predecessor_reuse_ref"]
    applied = action(r, reviewed_digest=result["manifest"]["manifest_digest"],
                     approval_ref="owner:third", reuse_rationale="same native Task obligations")
    assert applied["ok"], applied
    events = r.runtime.event_log.read_all()
    assert len([e for e in events if e.type == "task.done"]) == 1
    assert task_pipeline_generation_contexts(events)["TASK-1"]["generation_id"] == "old"
    r.emit("candidate.ready", {**r.candidate.payload, **r.source.payload})
    from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority
    fields = prepare_candidate_verification_authority(r.runtime, payload={},
        workflow_run_id=r.source.correlation_id, task_id="FLOW-ANCHOR")
    contract = json.loads((r.runtime.state_dir / fields["contract_snapshot_ref"]).read_text())
    assert fields["task_map_generation"] == "generation-3"
    assert contract["child_authorities"][0]["task_map_generation"] == "generation-1"


def test_damaged_predecessor_reuse_blocks_third_plan(recovery):
    r = recovery
    next_revision(r)
    event = next(e for e in r.runtime.event_log.read_all() if e.type == "task.status_changed"
                 and e.payload.get("source") == "plan_completion_reuse")
    (r.runtime.state_dir / event.payload["completion_reuse_ref"]["ref"]).write_text('{}')
    assert not action(r, dry_run=True)["ok"]
    assert r.runtime.task_store.get("TASK-1").status == "backlog"


def test_multiple_reviewed_hops_recheck_earliest_retained_decision(recovery):
    r = recovery
    next_revision(r)
    next_revision(r, "generation-4")
    digest = reviewed(r)
    assert action(r, reviewed_digest=digest, approval_ref="owner:fourth", reuse_rationale="same Task gates")["ok"]
    candidate = r.emit("candidate.ready", {**r.candidate.payload, **r.source.payload})
    from zf.runtime.plan_completion_reuse import current_proofs
    proofs = current_proofs(r.runtime, r.runtime.event_log.read_all(), candidate)
    assert proofs["TASK-1"]["task_map_generation"] == "generation-1"
    first = next(e for e in r.runtime.event_log.read_all() if e.type == "task.status_changed"
                 and e.payload.get("source") == "plan_completion_reuse")
    (r.runtime.state_dir / first.payload["completion_reuse_ref"]["ref"]).write_text('{}')
    with pytest.raises(ValueError):
        current_proofs(r.runtime, r.runtime.event_log.read_all(), candidate)


def test_original_native_result_still_required_after_prior_reuse(recovery):
    r = recovery
    next_revision(r)
    (r.runtime.state_dir / r.verify.payload["control_result_ref"]["ref"]).write_text('{}')
    assert not action(r, dry_run=True)["ok"]
    assert r.runtime.task_store.get("TASK-1").status == "backlog"


BROWSER = {"id": "browser", "command": "node browser.cjs", "owner": "candidate_verify"}


@pytest.mark.parametrize("recovery", [{"commands": [BROWSER]}], indirect=True)
def test_final_evidence_additions_need_separate_review(recovery):
    r = recovery
    task = r.runtime.task_store.get("TASK-1")
    task.contract.validation["commands"][0]["producer_paths"] = ["trace.zip"]
    r.runtime.task_store.reopen(task)
    result = action(r, dry_run=True)
    assert result["ok"], result
    assert result["manifest"]["tasks"][0]["candidate_evidence_additions"]
    args = {"reviewed_digest": result["manifest"]["manifest_digest"], "approval_ref": "owner:final-metadata",
            "reuse_rationale": "Task gates unchanged; new Candidate obligations still need fresh verification"}
    assert not action(r, **args)["ok"]
    assert r.runtime.task_store.get("TASK-1").status == "backlog"
    assert action(r, **args, candidate_evidence_review={"TASK-1": "Final Candidate must produce trace.zip"})["ok"]


@pytest.mark.parametrize("fault", ["command", "local_owner", "audit", "removed_path", "scope", "owner", "criteria"])
def test_evidence_addition_cannot_waive_task_gates(recovery, fault):
    task = recovery.runtime.task_store.get("TASK-1")
    from zf.runtime.plan_completion_lineage import completion_evidence_additions
    old = deepcopy(task)
    old.contract.validation = {"commands": [deepcopy(BROWSER)]}
    task.contract.validation = {"commands": [{**BROWSER, "producer_paths": ["trace.zip"]}]}
    if fault == "command": task.contract.validation["commands"][0]["command"] = "another command"
    elif fault == "local_owner":
        old.contract.validation["commands"][0]["owner"] = "task_verify"
        task.contract.validation["commands"][0]["owner"] = "task_verify"
    elif fault == "audit":
        old.contract.evidence_contract["task_pipeline_entry_mode"] = "verify_only"
        task.contract.evidence_contract["task_pipeline_entry_mode"] = "verify_only"
    elif fault == "removed_path": old.contract.validation["commands"][0]["producer_paths"] = ["required.png"]
    elif fault == "scope": task.contract.scope = ["extra.py"]
    elif fault == "owner": task.contract.validation["commands"][0]["owner"] = "impl_self_check"
    elif fault == "criteria": task.contract.acceptance_criteria = [{"id": "extra", "description": "changed"}]
    with pytest.raises(ValueError, match="acceptance/execution"):
        completion_evidence_additions(old, task)
