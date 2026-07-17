from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_admission import CallResultAdmissionService
from zf.runtime.call_result_envelope import (
    normalize_call_result_envelope,
    write_immutable_json_sidecar,
)
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    load_workflow_operation,
)


def _runtime(tmp_path: Path) -> tuple[CallResultAdmissionService, WorkflowOperationService]:
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    operations = WorkflowOperationService(
        state_dir=tmp_path,
        event_log=log,
        event_writer=writer,
    )
    admission = CallResultAdmissionService(
        state_dir=tmp_path,
        event_log=log,
        event_writer=writer,
        operation_service=operations,
    )
    return admission, operations


def _append_admitted_upstream(
    tmp_path: Path,
    operations: WorkflowOperationService,
    *,
    generation: str = "generation-1",
    target_commit: str = "c" * 40,
) -> str:
    control = write_immutable_json_sidecar(
        tmp_path,
        {"schema_version": "test-result.v1", "status": "passed"},
        root="call-results/control",
        kind="test_control_result",
        schema_version="test-result.v1",
        created_by="test",
    )
    envelope = normalize_call_result_envelope(
        source_payload={
            "run_id": "attempt-upstream-1",
            "role_instance": "verify-lane-0",
            "task_map_generation": generation,
            "target_commit": target_commit,
        },
        control_result={
            "schema_version": "test-result.v1",
            "ref": control["ref"],
            "sha256": control["sha256"],
        },
        workflow_run_id="run-1",
        operation_id="upstream-op-1",
        request_hash="upstream-request-1",
        source_event_id="upstream-result-1",
        source_event_type="verify.child.completed",
        actor="verify-lane-0",
    )
    descriptor = write_immutable_json_sidecar(
        tmp_path,
        envelope,
        root="call-results/envelopes",
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="test",
    )
    operations.event_log.append(ZfEvent(
        type="workflow.call.result.admitted",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "envelope_ref": descriptor,
        },
    ))
    return str(descriptor["ref"])


def _verification_payload(verdict: str = "rejected") -> dict:
    status = "failed" if verdict == "rejected" else "passed"
    return {
        "workflow_run_id": "run-1",
        "task_id": "T1",
        "run_id": "attempt-1",
        "stage_id": "verify",
        "role_instance": "verify-1",
        "contract_revision": "contract-1",
        "task_map_generation": "generation-1",
        "base_commit": "base-1",
        "task_ref": "artifacts/task-ref.json",
        "contract_snapshot_ref": "artifacts/contract.json",
        "contract_snapshot_digest": "a" * 64,
        "target_snapshot_ref": "artifacts/target.json",
        "target_snapshot_digest": "b" * 64,
        "target_commit": "target-1",
        "verification_result": {
            "schema_version": "verification-result.v1",
            "execution_status": "completed",
            "verdict": verdict,
            "failure_class": "product_rejection" if verdict == "rejected" else "none",
            "workflow_run_id": "run-1",
            "task_id": "T1",
            "contract_revision": "contract-1",
            "task_map_generation": "generation-1",
            "base_commit": "base-1",
            "task_ref": "artifacts/task-ref.json",
            "contract_snapshot_ref": "artifacts/contract.json",
            "contract_snapshot_digest": "a" * 64,
            "target_snapshot_ref": "artifacts/target.json",
            "target_snapshot_digest": "b" * 64,
            "target_commit": "target-1",
            "verification_owner": "task_verify",
            "verification_tier": "runtime",
            "requirement_results": [{
                "acceptance_id": "AC-1",
                "status": status,
                "verification_owner": "task_verify",
                "verification_tier": "runtime",
                "evidence_refs": ["test:one"],
                "findings": [{"message": "not complete"}] if status == "failed" else [],
                "reproduction_commands": ["pytest"],
            }],
        },
    }


def _goal_closure_payload(
    *,
    claim_descriptor: dict,
    generation: str = "generation-1",
    upstream_ref: str = "artifacts/call-results/upstream.json",
) -> dict:
    result = {
        "schema_version": "goal-closure-result.v1",
        "workflow_run_id": "run-1",
        "goal_id": "GOAL-1",
        "flow_kind": "prd",
        "task_map_generation": generation,
        "target_commit": "c" * 40,
        "objective_ref": "docs/prd.md",
        "goal_claim_set_ref": claim_descriptor["ref"],
        "goal_claim_set_digest": claim_descriptor["sha256"],
        "planning_result_ref": "artifacts/task-map.json",
        "candidate_ref": "candidate/GOAL-1",
        "closure_fact_ref": "artifacts/goal-closure/fact.json",
        "closure_fact_digest": "d" * 64,
        "input_result_refs": [upstream_ref],
        "goal_coverage": [{
            "goal_claim_id": "GOAL-AC-1",
            "status": "closed",
            "supporting_result_refs": [upstream_ref],
        }],
        "open_gap_refs": [],
        "verdict": "passed",
        "recommended_action": "complete",
        "summary": "all Goal claims are closed",
    }
    return {
        "workflow_run_id": "run-1",
        "run_id": "attempt-judge-1",
        "stage_id": "flow-final-judge",
        "role_instance": "judge-prd",
        "task_map_generation": generation,
        "contract_snapshot_ref": "artifacts/goal-closure/contract.json",
        "contract_snapshot_digest": "a" * 64,
        "target_snapshot_ref": "artifacts/goal-closure/target.json",
        "target_snapshot_digest": "b" * 64,
        "target_commit": "c" * 40,
        "goal_closure_result": result,
    }


def test_admitted_rejected_result_settles_call_without_semantic_rework(tmp_path: Path) -> None:
    admission, operations = _runtime(tmp_path)
    ensured = operations.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-1",
        operation_type="agent",
        request={"prompt": "verify"},
        task_id="T1",
    )
    event = ZfEvent(
        type="verify.child.completed",
        actor="verify-1",
        task_id="T1",
        payload=_verification_payload(),
    )
    outcome = admission.report_legacy_result(
        event,
        mode="blocking",
        operation={
            "workflow_run_id": "run-1",
            "operation_id": "op-1",
            "request_hash": ensured.request_hash,
        },
    )
    assert outcome.admitted is True
    events = operations.event_log.read_all()
    assert any(event.type == "workflow.operation.settled" for event in events)
    assert not any("rework" in event.type for event in events)
    admitted = next(event for event in events if event.type == "workflow.call.result.admitted")
    assert admitted.payload["semantic_verdict"] == "rejected"


def test_malformed_result_uses_output_repair_without_semantic_attempt(tmp_path: Path) -> None:
    admission, operations = _runtime(tmp_path)
    ensured = operations.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-1",
        operation_type="agent",
        request={"prompt": "verify"},
        task_id="T1",
    )
    payload = _verification_payload()
    payload["verification_result"].pop("target_commit")
    event = ZfEvent(
        type="verify.child.completed",
        actor="verify-1",
        task_id="T1",
        payload=payload,
    )
    outcome = admission.report_legacy_result(
        event,
        mode="blocking",
        operation={
            "workflow_run_id": "run-1",
            "operation_id": "op-1",
            "request_hash": ensured.request_hash,
        },
    )
    assert outcome.repair_requested is True
    repair = next(
        item for item in operations.event_log.read_all()
        if item.type == "workflow.call.result.repair.requested"
    )
    assert repair.payload["semantic_attempt_incremented"] is False
    assert not any("task.attempt" in item.type for item in operations.event_log.read_all())


def test_replayed_malformed_result_reuses_pending_repair_round(tmp_path: Path) -> None:
    admission, operations = _runtime(tmp_path)
    ensured = operations.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-1",
        operation_type="agent",
        request={"prompt": "verify"},
        task_id="T1",
    )
    payload = _verification_payload()
    payload["verification_result"].pop("target_commit")
    event = ZfEvent(
        type="verify.child.completed",
        actor="verify-1",
        task_id="T1",
        payload=payload,
    )
    operation = {
        "workflow_run_id": "run-1",
        "operation_id": "op-1",
        "request_hash": ensured.request_hash,
    }

    first = admission.report_legacy_result(
        event,
        mode="blocking",
        operation=operation,
    )
    replay = admission.report_legacy_result(
        event,
        mode="blocking",
        operation=operation,
    )

    assert first.repair_round == replay.repair_round == 1
    assert first.correction_dispatch_required is True
    assert replay.correction_dispatch_required is False
    repairs = [
        item for item in operations.event_log.read_all()
        if item.type == "workflow.call.result.repair.requested"
    ]
    assert len(repairs) == 1
    assert repairs[0].payload["result_digest"]


def test_selected_fanout_aggregate_without_child_refs_requires_output_repair(
    tmp_path: Path,
) -> None:
    admission, operations = _runtime(tmp_path)
    ensured = operations.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-fanout-1",
        operation_type="workflow",
        request={"pattern_id": "nested-review"},
        task_id="T1",
    )
    event = ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        task_id="T1",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "workflow_operation_id": "op-fanout-1",
            "workflow_operation_request_hash": ensured.request_hash,
            "fanout_id": "fanout-1",
            "stage_id": "nested-review",
            "status": "completed",
            "child_call_result_refs": [],
        },
    )

    outcome = admission.report_legacy_result(
        event,
        mode="blocking",
        operation={
            "workflow_run_id": "run-1",
            "operation_id": "op-fanout-1",
            "request_hash": ensured.request_hash,
        },
    )

    assert outcome.status == "repair_pending"
    assert any(
        issue["field"] == "control_result.child_call_result_refs"
        for issue in outcome.issues
    )
    operation = load_workflow_operation(operations.event_log, "op-fanout-1")
    assert operation is not None
    assert operation["status"] != "settled"


def test_goal_closure_admission_accepts_current_identity_and_rejects_stale(
    tmp_path: Path,
) -> None:
    admission, operations = _runtime(tmp_path)
    claim_descriptor = write_immutable_json_sidecar(
        tmp_path,
        {
            "schema_version": "goal-claim-set.v1",
            "workflow_run_id": "run-1",
            "goal_id": "GOAL-1",
            "task_map_generation": "generation-1",
            "claim_set_digest": "content-digest",
            "claims": [{
                "goal_claim_id": "GOAL-AC-1",
                "mandatory": True,
                "text": "deliver the requested behavior",
            }],
        },
        root="goal-closure/claim-sets",
        kind="goal_claim_set",
        schema_version="goal-claim-set.v1",
        created_by="test",
    )
    upstream_ref = _append_admitted_upstream(tmp_path, operations)

    def append_closure(generation: str) -> None:
        operations.event_log.append(ZfEvent(
            type="flow.goal.closed",
            correlation_id="run-1",
            payload={
                "workflow_run_id": "run-1",
                "goal_id": "GOAL-1",
                "task_map_generation": generation,
                "candidate_head_commit": "c" * 40,
                "goal_claim_set_ref": claim_descriptor["ref"],
                "goal_claim_set_digest": claim_descriptor["sha256"],
                "closure_fact_ref": "artifacts/goal-closure/fact.json",
                "closure_fact_digest": "d" * 64,
            },
        ))

    append_closure("generation-1")
    first_operation = operations.ensure_operation(
        workflow_run_id="run-1",
        operation_id="goal-judge-1",
        operation_type="agent",
        request={"goal": "GOAL-1", "generation": "generation-1"},
    )
    current = admission.report_legacy_result(
        ZfEvent(
            type="judge.child.completed",
            actor="judge-prd",
            correlation_id="run-1",
            payload=_goal_closure_payload(
                claim_descriptor=claim_descriptor,
                upstream_ref=upstream_ref,
            ),
        ),
        mode="blocking",
        operation={
            "workflow_run_id": "run-1",
            "operation_id": "goal-judge-1",
            "request_hash": first_operation.request_hash,
        },
    )
    assert current.admitted is True

    append_closure("generation-2")
    stale_operation = operations.ensure_operation(
        workflow_run_id="run-1",
        operation_id="goal-judge-stale",
        operation_type="agent",
        request={"goal": "GOAL-1", "generation": "generation-1"},
    )
    stale = admission.report_legacy_result(
        ZfEvent(
            type="judge.child.completed",
            actor="judge-prd",
            correlation_id="run-1",
            payload=_goal_closure_payload(
                claim_descriptor=claim_descriptor,
                upstream_ref=upstream_ref,
            ),
        ),
        mode="blocking",
        operation={
            "workflow_run_id": "run-1",
            "operation_id": "goal-judge-stale",
            "request_hash": stale_operation.request_hash,
        },
    )

    assert stale.repair_requested is True
    assert any(
        issue["code"] == "stale_closure_identity"
        and issue["field"] == "control_result.task_map_generation"
        for issue in stale.issues
    )


def test_goal_closure_admission_requires_active_waiver(tmp_path: Path) -> None:
    admission, operations = _runtime(tmp_path)
    claim_descriptor = write_immutable_json_sidecar(
        tmp_path,
        {
            "schema_version": "goal-claim-set.v1",
            "workflow_run_id": "run-1",
            "goal_id": "GOAL-1",
            "task_map_generation": "generation-1",
            "claim_set_digest": "content-digest",
            "claims": [{
                "goal_claim_id": "GOAL-AC-1",
                "mandatory": True,
                "text": "deliver the requested behavior",
            }],
        },
        root="goal-closure/claim-sets",
        kind="goal_claim_set",
        schema_version="goal-claim-set.v1",
        created_by="test",
    )
    upstream_ref = _append_admitted_upstream(tmp_path, operations)
    operations.event_log.append(ZfEvent(
        type="flow.goal.closed",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "goal_id": "GOAL-1",
            "task_map_generation": "generation-1",
            "candidate_head_commit": "c" * 40,
            "goal_claim_set_ref": claim_descriptor["ref"],
            "goal_claim_set_digest": claim_descriptor["sha256"],
            "closure_fact_ref": "artifacts/goal-closure/fact.json",
            "closure_fact_digest": "d" * 64,
        },
    ))
    operations.event_log.append(ZfEvent(
        id="waiver-event-1",
        type="verification.waived",
        actor="operator",
        payload={
            "task_ids": ["GOAL-AC-1"],
            "signature": "waiver:goal-ac-1",
            "reason": "approved product exception",
        },
    ))
    payload = _goal_closure_payload(
        claim_descriptor=claim_descriptor,
        upstream_ref=upstream_ref,
    )
    coverage = payload["goal_closure_result"]["goal_coverage"][0]
    coverage.update({"status": "waived", "waiver_ref": "waiver:goal-ac-1"})
    first_operation = operations.ensure_operation(
        workflow_run_id="run-1",
        operation_id="goal-judge-waived",
        operation_type="agent",
        request={"goal": "GOAL-1", "waiver": "active"},
    )

    admitted = admission.report_legacy_result(
        ZfEvent(
            type="judge.child.completed",
            actor="judge-prd",
            correlation_id="run-1",
            payload=payload,
        ),
        mode="blocking",
        operation={
            "workflow_run_id": "run-1",
            "operation_id": "goal-judge-waived",
            "request_hash": first_operation.request_hash,
        },
    )
    assert admitted.admitted is True

    operations.event_log.append(ZfEvent(
        type="verification.waiver.revoked",
        actor="operator",
        payload={
            "task_ids": ["GOAL-AC-1"],
            "signature": "waiver:goal-ac-1",
        },
    ))
    revoked_operation = operations.ensure_operation(
        workflow_run_id="run-1",
        operation_id="goal-judge-revoked-waiver",
        operation_type="agent",
        request={"goal": "GOAL-1", "waiver": "revoked"},
    )
    revoked = admission.report_legacy_result(
        ZfEvent(
            type="judge.child.completed",
            actor="judge-prd",
            correlation_id="run-1",
            payload=payload,
        ),
        mode="blocking",
        operation={
            "workflow_run_id": "run-1",
            "operation_id": "goal-judge-revoked-waiver",
            "request_hash": revoked_operation.request_hash,
        },
    )

    assert revoked.repair_requested is True
    assert any(issue["code"] == "waiver_not_active" for issue in revoked.issues)


def test_goal_closure_admission_rejects_stale_admitted_input(
    tmp_path: Path,
) -> None:
    admission, operations = _runtime(tmp_path)
    claim_descriptor = write_immutable_json_sidecar(
        tmp_path,
        {
            "schema_version": "goal-claim-set.v1",
            "workflow_run_id": "run-1",
            "goal_id": "GOAL-1",
            "task_map_generation": "generation-1",
            "claim_set_digest": "content-digest",
            "claims": [{
                "goal_claim_id": "GOAL-AC-1",
                "mandatory": True,
                "text": "deliver the requested behavior",
            }],
        },
        root="goal-closure/claim-sets",
        kind="goal_claim_set",
        schema_version="goal-claim-set.v1",
        created_by="test",
    )
    stale_ref = _append_admitted_upstream(
        tmp_path,
        operations,
        generation="generation-0",
        target_commit="b" * 40,
    )
    operations.event_log.append(ZfEvent(
        type="flow.goal.closed",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "goal_id": "GOAL-1",
            "task_map_generation": "generation-1",
            "candidate_head_commit": "c" * 40,
            "goal_claim_set_ref": claim_descriptor["ref"],
            "goal_claim_set_digest": claim_descriptor["sha256"],
            "closure_fact_ref": "artifacts/goal-closure/fact.json",
            "closure_fact_digest": "d" * 64,
        },
    ))
    operation = operations.ensure_operation(
        workflow_run_id="run-1",
        operation_id="goal-judge-stale-input",
        operation_type="agent",
        request={"goal": "GOAL-1", "generation": "generation-1"},
    )

    outcome = admission.report_legacy_result(
        ZfEvent(
            type="judge.child.completed",
            actor="judge-prd",
            correlation_id="run-1",
            payload=_goal_closure_payload(
                claim_descriptor=claim_descriptor,
                upstream_ref=stale_ref,
            ),
        ),
        mode="blocking",
        operation={
            "workflow_run_id": "run-1",
            "operation_id": "goal-judge-stale-input",
            "request_hash": operation.request_hash,
        },
    )

    assert outcome.repair_requested is True
    assert any(
        issue["code"] == "result_not_admitted"
        and issue["field"] == "control_result.input_result_refs"
        for issue in outcome.issues
    )


def test_invalid_event_replay_is_idempotent(tmp_path: Path) -> None:
    """ZF-REVIEW-140-B2:repair cap 耗尽产生 invalid 后,restart 清扫重放
    同一 terminal 事件不得追加第二个 workflow.call.result.invalid。"""
    admission, operations = _runtime(tmp_path)
    ensured = operations.ensure_operation(
        workflow_run_id="run-1", operation_id="op-1",
        operation_type="agent", request={"prompt": "verify"}, task_id="T1",
    )
    operation = {"workflow_run_id": "run-1", "operation_id": "op-1",
                 "request_hash": ensured.request_hash}
    for marker in ("m1", "m2", "m3"):
        payload = _verification_payload()
        payload["verification_result"].pop("target_commit")
        payload["verification_result"]["marker"] = marker
        event = ZfEvent(type="verify.child.completed", actor="verify-1",
                        task_id="T1", payload=payload)
        admission.report_legacy_result(event, mode="blocking", operation=operation)
    invalids = [e for e in operations.event_log.read_all()
                if e.type == "workflow.call.result.invalid"]
    assert len(invalids) == 1

    replay_payload = _verification_payload()
    replay_payload["verification_result"].pop("target_commit")
    replay_payload["verification_result"]["marker"] = "m3"
    replay = ZfEvent(type="verify.child.completed", actor="verify-1",
                     task_id="T1", payload=replay_payload)
    outcome = admission.report_legacy_result(replay, mode="blocking", operation=operation)
    assert outcome.status == "invalid"
    invalids2 = [e for e in operations.event_log.read_all()
                 if e.type == "workflow.call.result.invalid"]
    assert len(invalids2) == 1, "重放不得累积重复 invalid 事件"
