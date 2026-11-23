"""Reviewed completion reuse must survive Reader input and result admission."""
from copy import deepcopy

import pytest

from tests.test_plan_completion_reuse import recovery, action, reviewed
from zf.runtime.artifact_input_lifecycle import input_lifecycle
from zf.runtime.artifact_input_recovery import build_input_recovery
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_runtime import prepare_call_operation, mark_call_operation_started
from zf.runtime.candidate_currentness import candidate_currentness_issues
from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import write_task_contract_snapshot
from zf.runtime.task_pipeline_briefing import verification_result_template


def reader(r):
    provision_role_submit_credential(r.runtime.state_dir, "verify-lane-0")
    assert action(r, reviewed_digest=reviewed(r), approval_ref="owner", reuse_rationale="reviewed")["ok"]
    # The completion-unit fixture's minimal admission lacks the generation
    # fields required by the actual Reader handoff.
    r.emit("plan.artifact_package.admitted", {
        "plan_revision": "generation-2", "task_map_generation": "generation-2",
        "package_ref": r.source.payload["plan_artifact_package_ref"],
        "package_digest": r.source.payload["plan_artifact_package_digest"],
        "package_id": "planpkg-" + r.source.payload["plan_artifact_package_digest"],
    })
    freeze = hydrate_sidecar_ref(r.runtime.state_dir, r.candidate.payload["freeze_receipt_ref"]).payload
    freeze = {**freeze, **r.source.payload, "freeze_id": "freeze-new-plan",
        "plan_artifact_package_id": "planpkg-" + r.source.payload["plan_artifact_package_digest"]}
    freeze_ref = r.sidecar(freeze)
    candidate = r.emit("candidate.ready", {**r.candidate.payload, **freeze,
        "freeze_receipt_ref": freeze_ref, "freeze_receipt_digest": freeze_ref["sha256"]})
    payload = {**candidate.payload, "task_id": "FLOW-ANCHOR", "fanout_id": "candidate-readers",
        "child_id": "verify-lane-0", "role_instance": "verify-lane-0", "stage_id": "issue-lanes-verify",
        "output_profile_id": "candidate-verify", "artifact_package_mode": "shadow",
        "canonical_success_event": "test.passed", "canonical_failure_event": "test.failed"}
    prepared = prepare_call_operation(r.runtime, payload=payload, operation_type="fanout_reader_child",
        operation_key="verify-lane-0", stage_id="issue-lanes-verify", task_id="FLOW-ANCHOR",
        dispatch_id="candidate-dispatch", causation_id=candidate.id)
    mark_call_operation_started(r.runtime, prepared, task_id="FLOW-ANCHOR",
        dispatch_id="candidate-dispatch", causation_id=candidate.id)
    manifest = hydrate_sidecar_ref(r.runtime.state_dir, payload["attempt_source_manifest"]).payload
    return payload, prepared, manifest


def issues(r, identity):
    return candidate_currentness_issues(r.runtime.state_dir, r.runtime.event_log.read_all(), identity=identity,
        contract_ref=identity["contract_snapshot_ref"], contract_digest=identity["contract_snapshot_digest"])


def test_reused_candidate_input_recovery_is_current(recovery):
    r = recovery
    payload, _, manifest = reader(r)
    assert issues(r, payload) == []
    lifecycle = input_lifecycle(manifest, r.runtime.event_log.read_all(), state_dir=r.runtime.state_dir)
    assert lifecycle["status"] == "current" and not lifecycle["requires_rebind"]
    report = build_input_recovery(state_dir=r.runtime.state_dir, manifest=manifest,
        identity={"actor": "verify-lane-0", "role": "verify-lane-0", "purpose": "candidate-verify"},
        events=r.runtime.event_log.read_all(), required=payload["required_reads"], reads=[])
    assert report["status"] == "bound" and report["next_action"] == "read_missing_required_inputs"
    assert report["authorizes_execution"] is False


def test_reused_candidate_result_admission_consumes_same_proof(recovery):
    r = recovery
    payload, prepared, manifest = reader(r)
    for requirement in payload["required_reads"]:
        read_attempt_artifact(r.runtime.state_dir, manifest=manifest,
            source_id=requirement["source_id"], artifact_id=requirement["artifact_id"],
            json_path=requirement["json_path"])
    contract = hydrate_sidecar_ref(r.runtime.state_dir,
        {"ref": payload["contract_snapshot_ref"], "sha256": payload["contract_snapshot_digest"]}).payload
    semantic = verification_result_template(contract)
    semantic.update(summary="isolated mock acceptance", evidence_refs=["event:mock-check"])
    for probe in semantic["probe_receipts"]:
        probe.update(target_commit=payload["target_commit"], evidence_refs=["event:mock-command"])
    for result in semantic["requirement_results"]:
        result["evidence_refs"] = ["event:mock-acceptance"]
    token = provision_role_submit_credential(r.runtime.state_dir, "verify-lane-0").read_text().strip()
    submitted = SemanticResultSubmitService(state_dir=r.runtime.state_dir, event_log=r.runtime.event_log,
        event_writer=r.runtime.event_writer).submit(operation_id=prepared.operation_id,
        semantic_result=semantic, role_instance="verify-lane-0", credential=token)
    assert submitted.admitted_event_id


@pytest.mark.parametrize("fault", ["changed_contract", "changed_authority", "not_done", "proof_bytes",
    "proof_ref", "missing_ref", "verification", "old_identity", "new_candidate"])
def test_reuse_never_waives_currentness(recovery, fault):
    r = recovery
    payload, _, manifest = reader(r)
    contract = hydrate_sidecar_ref(r.runtime.state_dir,
        {"ref": payload["contract_snapshot_ref"], "sha256": payload["contract_snapshot_digest"]}).payload
    child = contract["child_authorities"][0]
    if fault in {"changed_contract", "changed_authority", "not_done"}:
        task = r.runtime.task_store.get("TASK-1")
        if fault == "changed_contract": task.contract.verification = "different command"
        elif fault == "changed_authority": task.contract_authority_revision = "changed"
        task.status = "backlog"
        r.runtime.task_store.reopen(task)
        if fault != "not_done":
            r.runtime.task_store.update(task.id, status="done", completed_at=task.completed_at)
    elif fault == "proof_bytes":
        (r.runtime.state_dir / child["completion_reuse_ref"]["ref"]).write_text("{}")
    elif fault == "new_candidate":
        r.emit("candidate.ready", {**r.candidate.payload, **r.source.payload, "candidate_head_commit": "d" * 40})
    else:
        changed = deepcopy(contract)
        entry = changed["child_authorities"][0]
        if fault == "proof_ref": entry["completion_reuse_ref"] = {"ref": "other", "sha256": "x"}
        elif fault == "missing_ref": entry.pop("completion_reuse_ref")
        elif fault == "verification": entry["verification_event_id"] = "unreviewed-verification"
        else: entry["contract_authority_revision"] = "unreviewed-old-authority"
        descriptor = write_task_contract_snapshot(r.runtime.state_dir, changed)
        # Supply a correctly digest-bound changed snapshot; currentness must still reject it.
        from zf.runtime.task_contract_snapshot import write_target_snapshot, build_target_snapshot
        target = write_target_snapshot(r.runtime.state_dir,
            build_target_snapshot(descriptor, target_commit=payload["target_commit"], contract_snapshot=changed))
        payload.update(contract_snapshot_ref=descriptor["ref"], contract_snapshot_digest=descriptor["sha256"],
                       target_snapshot_ref=target["ref"], target_snapshot_digest=target["sha256"])
    assert issues(r, payload)
    if fault not in {"proof_ref", "missing_ref", "verification", "old_identity"}:
        assert input_lifecycle(manifest, r.runtime.event_log.read_all(), state_dir=r.runtime.state_dir)["requires_rebind"]
