"""A frozen Candidate carries only admitted duties on its own ancestry."""
import pytest

from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def contract(runtime, freeze):
    payload = {"candidate_snapshot_event_id": freeze.id}
    prepare_candidate_verification_authority(runtime, payload=payload, workflow_run_id=freeze.payload["workflow_run_id"],
                                            task_id="FLOW-ANCHOR")
    ref = {"ref":payload["contract_snapshot_ref"], "sha256":payload["contract_snapshot_digest"]}
    return ref["sha256"], hydrate_sidecar_ref(runtime.state_dir, ref).payload


def successor(runtime, predecessor, head, base, branch=None, **fields):
    p = predecessor.payload
    receipt = hydrate_sidecar_ref(runtime.state_dir, p["freeze_receipt_ref"]).payload
    receipt = {**receipt, "candidate_generation": "successor-" + head, "freeze_id": "freeze-" + head,
               "candidate_base_commit":base, "candidate_head_commit":head, "candidate_head":head,
               "candidate_ref":branch or p["candidate_ref"], **fields}
    ref = write_immutable_json_sidecar(runtime.state_dir, receipt, root="candidate-freeze-receipts",
        kind="candidate_freeze_receipt", schema_version=receipt["schema_version"], created_by="test")
    return runtime.event_writer.append(ZfEvent(type="candidate.ready", origin="kernel", actor="zf-cli",
        correlation_id=p["workflow_run_id"], payload={**receipt,"freeze_receipt_ref":ref,"freeze_receipt_digest":ref["sha256"]}))


def setup(runtime):
    seeded = _seed_frozen_candidate(runtime, package_fields={"pdd_id":"FLOW-ANCHOR"})
    source = next(e for e in runtime.event_log.read_all() if e.type == "candidate.ready")
    return seeded, source


def marker(runtime, source, *, admitted=True, **changes):
    p = source.payload
    row = {"finding_id":"protected-owner", "operation_id":"old-reader", "disposition":{"classification":"contract_question",
           "owner":"planner", "next_action":"obtain authorized disposition"}, "status":"pending"}
    decision = write_immutable_json_sidecar(runtime.state_dir, {"decision":"return_to_plan"}, root="oa-decisions",
        kind="orchestration_decision", schema_version="orchestration-decision.v1", created_by="test")
    if admitted:
        runtime.event_writer.append(ZfEvent(type="orchestrator.semantic.decision.admitted", origin="kernel",
            causation_id="typed-oa-result", payload={"workflow_run_id":p["workflow_run_id"], "checkpoint":"semantic_failure",
            "operation_id":"oa-op", "decision":"return_to_plan", "decision_event_id":"typed-oa-result", "decision_ref":decision}))
    return runtime.event_writer.append(ZfEvent(type="orchestrator.replan_requested", origin="kernel", actor="zf-cli",
        causation_id="typed-oa-result", correlation_id=p["workflow_run_id"], payload={"classification":"oa_semantic_return_to_plan",
            "workflow_run_id":p["workflow_run_id"], "pdd_id":"FLOW-ANCHOR", "candidate_event_id":source.id,
            "target_ref":p["candidate_ref"], "task_map_generation":p["task_map_generation"], "source_commit":p["candidate_head_commit"],
            "candidate_failure_id":"agg-failure", "orchestrator_operation_id":"oa-op", "orchestrator_decision_ref":decision,
            "rework_summary":{"candidate_obligations":[row]}, **changes}))


def test_future_ledger_cannot_change_frozen_contract_or_drop_successor_obligation(tmp_path):
    runtime = _runtime(tmp_path)
    _, source = setup(runtime)
    baseline, _ = contract(runtime, source)
    duty = marker(runtime, source)
    ready = successor(runtime, source, "d"*40, source.payload["candidate_head_commit"])
    _, new_contract = contract(runtime, ready)
    assert [r["finding_id"] for r in new_contract.get("candidate_recovery_obligations", [])] == ["protected-owner"]
    runtime.event_writer.append(ZfEvent(type="task_map.ready", origin="kernel", payload={
        "workflow_run_id":source.payload["workflow_run_id"], "pdd_id":"FLOW-ANCHOR", "task_map_generation":"later",
        "source_commit":ready.payload["candidate_head_commit"], "candidate_ref":ready.payload["candidate_ref"],
        "rework_summary":duty.payload["rework_summary"]}))
    assert contract(runtime, source)[0] == baseline
    assert contract(runtime, ready)[0] != baseline


@pytest.mark.parametrize("fault", ["other_ref", "fork", "no_admission", "foreign_anchor", "bad_source", "wrong_generation"])
def test_unrelated_or_unadmitted_marker_does_not_cross_freeze(tmp_path, fault):
    runtime = _runtime(tmp_path)
    _, source = setup(runtime)
    changes = {"foreign_anchor":{"pdd_id":"OTHER"}, "bad_source":{"source_commit":"a"*40},
               "wrong_generation":{"task_map_generation":"stale"}}.get(fault, {})
    marker(runtime, source, admitted=fault != "no_admission", **changes)
    ready = successor(runtime, source, "d"*40, "f"*40 if fault == "fork" else source.payload["candidate_head_commit"],
        branch="refs/heads/unrelated" if fault == "other_ref" else None)
    assert not contract(runtime, ready)[1].get("candidate_recovery_obligations")


def test_old_freeze_excludes_later_task_map_and_oa_ack(tmp_path):
    runtime = _runtime(tmp_path)
    _, source = setup(runtime)
    baseline, _ = contract(runtime, source)
    later = marker(runtime, source)
    runtime.event_writer.append(ZfEvent(type="task_map.ready", origin="kernel", payload={
        **later.payload, "candidate_ref":source.payload["candidate_ref"]}))
    assert contract(runtime, source)[0] == baseline


@pytest.mark.parametrize("matching_plan", [True, False])
def test_same_head_new_generation_needs_exact_replan_input(tmp_path, matching_plan):
    from zf.runtime.task_contract_snapshot import build_target_snapshot, write_target_snapshot, write_task_contract_snapshot

    runtime = _runtime(tmp_path)
    _, source = setup(runtime)
    mark = marker(runtime, source)
    run = source.payload["workflow_run_id"]
    next_gen = "generation-2"
    # A Planner may change the contract while retaining the same implementation commit.
    request = runtime.event_writer.append(ZfEvent(type="prd.scan.completed", actor="zf-cli",
        correlation_id=run, payload={"workflow_run_id":run, "pdd_id":"FLOW-ANCHOR",
            "replan_classification":"oa_semantic_return_to_plan","rework_of":"agg-failure" if matching_plan else "other",
            "candidate_event_id":source.id, "orchestrator_decision_ref":mark.payload["orchestrator_decision_ref"]}))
    runtime.event_writer.append(ZfEvent(type="task.pipeline.generation.admitted", origin="kernel", actor="zf-cli",
        correlation_id=run, payload={"workflow_run_id":run,"pdd_id":"FLOW-ANCHOR","generation_id":"task-gen-2",
            "task_map_generation":next_gen, "trigger_event_id":request.id}))
    old = next(e for e in runtime.event_log.read_all() if e.type == "task.pipeline.verify.completed")
    child = hydrate_sidecar_ref(runtime.state_dir, {"ref":old.payload["contract_snapshot_ref"],
                                "sha256":old.payload["contract_snapshot_digest"]}).payload
    updated = {**child, "task_map_generation":next_gen}
    contract_ref = write_task_contract_snapshot(runtime.state_dir, updated)
    target = write_target_snapshot(runtime.state_dir, build_target_snapshot(contract_ref,
                target_commit=old.payload["target_commit"],contract_snapshot=updated))
    runtime.event_writer.append(ZfEvent(type=old.type, origin="kernel", task_id=old.task_id, correlation_id=run,
        payload={**old.payload, "task_map_generation":next_gen, "contract_snapshot_ref":contract_ref["ref"],
            "contract_snapshot_digest":contract_ref["sha256"], "target_snapshot_ref":target["ref"],
            "target_snapshot_digest":target["sha256"]}))
    after = successor(runtime, source, source.payload["candidate_head_commit"], source.payload["candidate_base_commit"],
                      task_map_generation=next_gen, generation_id="task-gen-2")
    rows = contract(runtime, after)[1].get("candidate_recovery_obligations", [])
    assert [r["finding_id"] for r in rows] == (["protected-owner"] if matching_plan else [])


def test_same_finding_carried_once_across_multiple_native_freezes(tmp_path):
    from zf.runtime.candidate_finding_partition import validate_obligation_results
    from zf.runtime.verification_result import VerificationResultError

    runtime = _runtime(tmp_path)
    _, source = setup(runtime)
    duty = marker(runtime, source)
    runtime.event_writer.append(ZfEvent(type="task_map.ready", origin="kernel", payload={
        "workflow_run_id":source.payload["workflow_run_id"], "pdd_id":"FLOW-ANCHOR",
        "source_commit":source.payload["candidate_head_commit"],"task_map_generation":source.payload["task_map_generation"],
        "candidate_ref":source.payload["candidate_ref"], "rework_summary":duty.payload["rework_summary"]}))
    middle = successor(runtime, source, "d"*40, source.payload["candidate_head_commit"])
    after = successor(runtime, middle, "e"*40, middle.payload["candidate_head_commit"])
    body = contract(runtime, after)[1]
    assert len(body["candidate_recovery_obligations"]) == 1
    with pytest.raises(VerificationResultError, match="every retained recovery obligation"):
        validate_obligation_results({"verdict":"passed"}, body)


def test_legacy_freeze_without_pdd_keeps_anchored_repair_map_obligation(tmp_path):
    runtime = _runtime(tmp_path)
    _seed_frozen_candidate(runtime)
    source = next(e for e in runtime.event_log.read_all() if e.type == "candidate.ready")
    assert "pdd_id" not in source.payload
    finding = {"finding_id":"legacy-unknown", "status":"pending", "disposition":{"classification":"execution_unknown"}}
    runtime.event_writer.append(ZfEvent(type="task_map.ready", origin="kernel", payload={
        "workflow_run_id":source.payload["workflow_run_id"],"pdd_id":"FLOW-ANCHOR","source_commit":source.payload["candidate_head_commit"],
        "candidate_ref":source.payload["candidate_ref"], "task_map_generation":source.payload["task_map_generation"],
        "rework_summary":{"candidate_obligations":[finding]}}))
    after = successor(runtime, source, "d"*40, source.payload["candidate_head_commit"])
    assert "pdd_id" not in after.payload
    assert contract(runtime, after)[1]["candidate_recovery_obligations"] == [finding]
