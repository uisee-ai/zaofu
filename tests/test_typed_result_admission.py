from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_read_ledger import source_manifest_from_payload
from zf.runtime.call_result_admission import CallResultAdmissionService
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_contract_snapshot import (
    build_target_snapshot,
    write_target_snapshot,
    write_task_contract_snapshot,
)
from zf.runtime.typed_result_admission import typed_result_admission_issues
from zf.runtime.writer_handoff_failure import (
    writer_call_result_failure_payload,
    writer_redispatch_block,
)


def _contract() -> dict:
    command = "npm test"
    return {
        "schema_version": "task-contract-snapshot.v1",
        "workflow_run_id": "run-1",
        "task_id": "TASK-1",
        "contract_revision": "contract-r1",
        "task_map_generation": "task-map-g1",
        "base_commit": "base-1",
        "task_ref": "refs/zf/tasks/TASK-1",
        "acceptance_criteria": [{
            "acceptance_id": "AC-1",
            "statement": "endpoint responds",
            "mandatory": True,
            "verification_owner": "task_verify",
            "verification_tier": "task_non_smoke",
            "verification_command_ids": ["unit-test"],
        }],
        "verification_command": command,
        "verification_commands": [{
            "command_id": "unit-test",
            "command": command,
            "command_digest": hashlib.sha256(command.encode()).hexdigest(),
            "acceptance_ids": ["AC-1"],
            "owner": "impl_self_check",
            "tier": "fast",
            "deterministic": True,
            "reusable": True,
            "timeout_seconds": 60,
        }],
        "verification_tiers": ["task_non_smoke"],
        "required_source_outputs": [],
        "required_contract_tests": [],
        "source_refs": {},
        "evidence_contract": {},
        "allowed_paths": ["src/**", "tests/**"],
        "protected_paths": [".zf/**"],
    }


def _descriptors(tmp_path: Path) -> tuple[dict, dict, dict]:
    contract = _contract()
    descriptor = write_task_contract_snapshot(tmp_path, contract)
    target = build_target_snapshot(
        descriptor,
        target_commit="target-1",
        contract_snapshot=contract,
    )
    target_descriptor = write_target_snapshot(tmp_path, target)
    return contract, descriptor, target_descriptor


def _implementation_result(contract: dict, descriptor: dict) -> dict:
    command = contract["verification_commands"][0]
    return {
        "schema_version": "implementation-result.v1",
        "workflow_run_id": "run-1",
        "task_id": "TASK-1",
        "attempt_id": "attempt-1",
        "contract_revision": "contract-r1",
        "task_map_generation": "task-map-g1",
        "contract_snapshot_ref": descriptor["ref"],
        "contract_snapshot_digest": descriptor["sha256"],
        "execution_status": "completed",
        "verdict": "passed",
        "target_commit": "c" * 40,
        "evidence_refs": ["event:impl-completed"],
        "self_check": {
            "schema_version": "impl-self-check.v1",
            "workflow_run_id": "run-1",
            "task_id": "TASK-1",
            "attempt_id": "attempt-1",
            "contract_revision": "contract-r1",
            "task_map_generation": "task-map-g1",
            "source_commit": "c" * 40,
            "target_commit": "c" * 40,
            "contract_snapshot_ref": descriptor["ref"],
            "contract_snapshot_digest": descriptor["sha256"],
            "command_receipts": [{
                "receipt_id": "receipt-unit",
                "command_id": "unit-test",
                "command_digest": command["command_digest"],
                "target_commit": "c" * 40,
                "status": "passed",
                "exit_code": 0,
                "evidence_refs": ["event:npm-test"],
            }],
            "acceptance_results": [{
                "acceptance_id": "AC-1",
                "status": "passed",
                "command_receipt_ids": ["receipt-unit"],
                "evidence_refs": ["event:ac-1"],
            }],
            "evidence_refs": ["event:impl-completed"],
        },
    }


@pytest.mark.parametrize("oid", ["a" * 40, "a" * 64, "a" * 7, "a" * 39,
                                    "a" * 41, "a" * 63, "a" * 65, "HEAD",
                                    "g" * 40, "A" * 40, " " + "a" * 40, ""])
def test_impl_exact_oid_admission(tmp_path: Path, oid: str) -> None:
    contract, descriptor, _ = _descriptors(tmp_path)
    result = _implementation_result(contract, descriptor)
    result["target_commit"] = oid
    result["self_check"].update(source_commit=oid, target_commit=oid)
    result["self_check"]["command_receipts"][0]["target_commit"] = oid
    issues = typed_result_admission_issues(
        tmp_path, schema_version="implementation-result.v1", result=result,
        semantic_submit=True,
    )
    expected = [] if oid in {"a" * 40, "a" * 64} else ["implementation_target_commit_invalid"]
    assert [issue["code"] for issue in issues] == expected


@pytest.mark.parametrize("boundary", ["legacy", "non-semantic", "failed", "blocked"])
def test_impl_oid_gate_preserves_non_success_and_legacy(tmp_path: Path, boundary: str) -> None:
    contract, descriptor, _ = _descriptors(tmp_path)
    result = _implementation_result(contract, descriptor)
    result["target_commit"] = "abc123"
    if boundary == "legacy":
        result.pop("contract_snapshot_ref")
        result.pop("contract_snapshot_digest")
    elif boundary == "failed":
        result["execution_status"] = "failed"
    elif boundary == "blocked":
        result["verdict"] = "blocked"
    assert typed_result_admission_issues(
        tmp_path, schema_version="implementation-result.v1", result=result,
        semantic_submit=boundary != "non-semantic",
    ) == []


def _verification_result(
    contract: dict,
    descriptor: dict,
    target_descriptor: dict,
) -> dict:
    command = contract["verification_commands"][0]
    return {
        "schema_version": "verification-result.v1",
        "workflow_run_id": "run-1",
        "task_id": "TASK-1",
        "contract_revision": "contract-r1",
        "task_map_generation": "task-map-g1",
        "base_commit": "base-1",
        "task_ref": "refs/zf/tasks/TASK-1",
        "contract_snapshot_ref": descriptor["ref"],
        "contract_snapshot_digest": descriptor["sha256"],
        "target_snapshot_ref": target_descriptor["ref"],
        "target_snapshot_digest": target_descriptor["sha256"],
        "target_commit": "target-1",
        "execution_status": "completed",
        "verdict": "passed",
        "verification_owner": "task_verify",
        "verification_tier": "task_non_smoke",
        "summary": "verified",
        "evidence_refs": ["artifact:verify-report"],
        "reused_command_receipt_ids": [],
        "probe_receipts": [{
            "probe_id": "independent-unit",
            "command_id": command["command_id"],
            "command": command["command"],
            "command_digest": command["command_digest"],
            "target_commit": "target-1",
            "status": "passed",
            "exit_code": 0,
            "evidence_refs": ["event:verify-test"],
        }],
        "rework_items": [],
        "requirement_results": [{
            "acceptance_id": "AC-1",
            "status": "passed",
            "verification_owner": "task_verify",
            "verification_tier": "task_non_smoke",
            "evidence_refs": ["event:verify-ac-1"],
            "findings": [],
            "reproduction_commands": ["npm test"],
        }],
    }


def test_impl_rejects_invented_command_but_accepts_contract_receipt(
    tmp_path: Path,
) -> None:
    contract, descriptor, _target_descriptor = _descriptors(tmp_path)
    valid = _implementation_result(contract, descriptor)
    assert typed_result_admission_issues(
        tmp_path,
        schema_version="implementation-result.v1",
        result=valid,
        semantic_submit=True,
    ) == []

    invalid = {**valid, "self_check": dict(valid["self_check"])}
    invalid["self_check"]["command_receipts"] = [
        {
            **valid["self_check"]["command_receipts"][0],
            "command_id": "root-npm-test",
        }
    ]
    issues = typed_result_admission_issues(
        tmp_path,
        schema_version="implementation-result.v1",
        result=invalid,
        semantic_submit=True,
    )
    assert [issue["code"] for issue in issues] == [
        "worker_result_command_unknown"
    ]
    assert issues[0]["recovery_owner"] == "implementation_owner"


def test_impl_self_check_allows_no_receipt_when_commands_are_later_owned(
    tmp_path: Path,
) -> None:
    contract = _contract()
    contract["verification_commands"][0]["owner"] = "task_verify"
    descriptor = write_task_contract_snapshot(tmp_path, contract)
    result = _implementation_result(contract, descriptor)
    result["self_check"]["command_receipts"] = []
    result["self_check"]["acceptance_results"][0]["command_receipt_ids"] = []

    assert typed_result_admission_issues(
        tmp_path,
        schema_version="implementation-result.v1",
        result=result,
        semantic_submit=True,
    ) == []


def test_secure_call_result_admission_wires_typed_impl_repair(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    contract, descriptor, _target_descriptor = _descriptors(state_dir)
    invalid = _implementation_result(contract, descriptor)
    invalid["self_check"] = dict(invalid["self_check"])
    invalid["self_check"]["command_receipts"] = [{
        **invalid["self_check"]["command_receipts"][0],
        "command_id": "root-npm-test",
    }]
    log = EventLog(state_dir / "events.jsonl")
    service = CallResultAdmissionService(
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
    )
    event = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-1",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": "operation-impl-1",
            "request_hash": "request-impl-1",
            "attempt_id": "attempt-1",
            "implementation_result": invalid,
        },
    )

    outcome = service.report_legacy_result(
        event,
        mode="blocking",
        operation={
            "workflow_run_id": "run-1",
            "operation_id": "operation-impl-1",
            "request_hash": "request-impl-1",
        },
        require_semantic_submit=True,
        semantic_submit=True,
    )

    assert outcome.repair_requested is True
    assert [issue["code"] for issue in outcome.issues] == [
        "worker_result_command_unknown"
    ]
    repair = next(
        event
        for event in log.read_all()
        if event.type == "workflow.call.result.repair.requested"
    )
    assert repair.payload["semantic_attempt_incremented"] is False


def test_verify_rejects_p15_empty_evidence_and_accepts_complete_receipts(
    tmp_path: Path,
) -> None:
    contract, descriptor, target_descriptor = _descriptors(tmp_path)
    valid = _verification_result(contract, descriptor, target_descriptor)
    assert typed_result_admission_issues(
        tmp_path,
        schema_version="verification-result.v1",
        result=valid,
        semantic_submit=True,
    ) == []

    invalid = {
        **valid,
        "evidence_refs": [],
        "probe_receipts": [{
            "probe_id": "independent-unit",
            "status": "passed",
            "evidence_refs": [],
        }],
    }
    issues = typed_result_admission_issues(
        tmp_path,
        schema_version="verification-result.v1",
        result=invalid,
        semantic_submit=True,
    )
    assert [issue["code"] for issue in issues] == [
        "verification_report_evidence_missing"
    ]
    assert issues[0]["recovery_owner"] == "task_verify"


def test_task_verify_does_not_require_candidate_owned_command_receipt(
    tmp_path: Path,
) -> None:
    contract = _contract()
    candidate_command = "npm run test:e2e"
    contract["verification_commands"].append({
        "command_id": "candidate-e2e",
        "command": candidate_command,
        "command_digest": hashlib.sha256(candidate_command.encode()).hexdigest(),
        "acceptance_ids": ["AC-1"],
        "owner": "candidate_verify",
        "tier": "real_e2e",
        "deterministic": True,
        "reusable": False,
        "timeout_seconds": 900,
    })
    contract["acceptance_criteria"][0]["verification_command_ids"].append(
        "candidate-e2e"
    )
    descriptor = write_task_contract_snapshot(tmp_path, contract)
    target = build_target_snapshot(
        descriptor,
        target_commit="target-1",
        contract_snapshot=contract,
    )
    target_descriptor = write_target_snapshot(tmp_path, target)
    result = _verification_result(contract, descriptor, target_descriptor)

    assert typed_result_admission_issues(
        tmp_path,
        schema_version="verification-result.v1",
        result=result,
        semantic_submit=True,
    ) == []


def test_candidate_verify_requires_its_local_candidate_command(
    tmp_path: Path,
) -> None:
    contract = _contract()
    contract["verification_owner"] = "candidate_verify"
    contract["verification_tier"] = "task_non_smoke"
    contract["acceptance_criteria"][0].update({
        "verification_owner": "candidate_verify",
        "verification_tier": "task_non_smoke",
    })
    contract["verification_commands"][0].update({
        "owner": "candidate_verify",
        "producer_task_id": "TASK-1",
    })
    descriptor = write_task_contract_snapshot(tmp_path, contract)
    target = build_target_snapshot(
        descriptor,
        target_commit="target-1",
        contract_snapshot=contract,
    )
    target_descriptor = write_target_snapshot(tmp_path, target)
    result = _verification_result(contract, descriptor, target_descriptor)
    result["verification_owner"] = "candidate_verify"
    result["verification_tier"] = "task_non_smoke"
    result["requirement_results"][0].update({
        "verification_owner": "candidate_verify",
        "verification_tier": "task_non_smoke",
    })

    assert typed_result_admission_issues(
        tmp_path,
        schema_version="verification-result.v1",
        result=result,
        semantic_submit=True,
    ) == []

    result["probe_receipts"] = [{
        "probe_id": "independent-release-audit",
        "status": "passed",
        "evidence_refs": ["event:independent-release-audit"],
    }]
    issues = typed_result_admission_issues(
        tmp_path,
        schema_version="verification-result.v1",
        result=result,
        semantic_submit=True,
    )
    assert [issue["code"] for issue in issues] == [
        "verification_canonical_command_coverage_missing"
    ]


def test_task_verify_does_not_require_another_tasks_producer_command(
    tmp_path: Path,
) -> None:
    contract, descriptor, target_descriptor = _descriptors(tmp_path)
    contract["verification_commands"][0]["producer_task_id"] = "TASK-EVIDENCE"
    descriptor = write_task_contract_snapshot(tmp_path, contract)
    target = build_target_snapshot(
        descriptor,
        target_commit="target-1",
        contract_snapshot=contract,
    )
    target_descriptor = write_target_snapshot(tmp_path, target)
    result = _verification_result(contract, descriptor, target_descriptor)
    result["contract_snapshot_ref"] = descriptor["ref"]
    result["contract_snapshot_digest"] = descriptor["sha256"]
    result["target_snapshot_ref"] = target_descriptor["ref"]
    result["target_snapshot_digest"] = target_descriptor["sha256"]
    result["probe_receipts"] = [{
        "probe_id": "independent-task-verify",
        "status": "passed",
        "evidence_refs": ["event:focused-check"],
    }]
    result["requirement_results"][0]["reproduction_commands"] = []

    assert typed_result_admission_issues(
        tmp_path,
        schema_version="verification-result.v1",
        result=result,
        semantic_submit=True,
    ) == []


def test_verify_rejects_substituted_or_unbound_canonical_command(
    tmp_path: Path,
) -> None:
    contract, descriptor, target_descriptor = _descriptors(tmp_path)
    valid = _verification_result(contract, descriptor, target_descriptor)

    unbound = {**valid, "probe_receipts": [{
        "probe_id": "independent-unit",
        "status": "passed",
        "evidence_refs": ["event:substituted-test"],
    }]}
    issues = typed_result_admission_issues(
        tmp_path,
        schema_version="verification-result.v1",
        result=unbound,
        semantic_submit=True,
    )
    assert [issue["code"] for issue in issues] == [
        "verification_canonical_command_coverage_missing"
    ]

    substituted = {**valid, "probe_receipts": [{
        **valid["probe_receipts"][0],
        "command": "npm test -- --runInBand",
    }]}
    issues = typed_result_admission_issues(
        tmp_path,
        schema_version="verification-result.v1",
        result=substituted,
        semantic_submit=True,
    )
    assert [issue["code"] for issue in issues] == [
        "verification_probe_command_mismatch"
    ]


def test_verify_pass_requires_zero_exit_canonical_receipt(
    tmp_path: Path,
) -> None:
    contract, descriptor, target_descriptor = _descriptors(tmp_path)
    valid = _verification_result(contract, descriptor, target_descriptor)
    failed = {**valid, "probe_receipts": [{
        **valid["probe_receipts"][0],
        "status": "failed",
        "exit_code": 1,
    }]}

    issues = typed_result_admission_issues(
        tmp_path,
        schema_version="verification-result.v1",
        result=failed,
        semantic_submit=True,
    )

    assert [issue["code"] for issue in issues] == [
        "verification_probe_command_not_passed"
    ]


def test_source_manifest_pins_admitted_and_control_result_digests(
    tmp_path: Path,
) -> None:
    envelope = write_sidecar_json(
        tmp_path,
        "artifacts/call-results/envelope.json",
        {"schema_version": "call-result-envelope.v1"},
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="test",
    )
    control = write_sidecar_json(
        tmp_path,
        "artifacts/call-results/control.json",
        {"schema_version": "verification-result.v1"},
        kind="call_control_result",
        schema_version="verification-result.v1",
        created_by="test",
    )
    manifest, _descriptor = source_manifest_from_payload(
        state_dir=tmp_path,
        project_root=tmp_path,
        payload={
            "admitted_call_result_ref": envelope,
            "control_result_ref": control,
        },
        workflow_run_id="run-1",
        task_id="TASK-1",
        attempt_id="attempt-verify",
        dispatch_id="dispatch-verify",
    )
    sources = {item["source_id"]: item for item in manifest["sources"]}
    assert sources["admitted-call-result"]["sha256"] == envelope["sha256"]
    assert sources["control-result"]["sha256"] == control["sha256"]


def test_writer_handoff_second_fingerprint_halts_and_fences_redispatch() -> None:
    issues = [{
        "field": "control_result.self_check",
        "code": "impl_self_check_invalid",
        "message": "receipt missing",
        "failure_owner": "worker_result",
        "recovery_owner": "implementation_owner",
        "recovery_action": "result_repair",
    }]
    first_payload = writer_call_result_failure_payload(
        [],
        task_id="TASK-1",
        contract_revision="contract-r1",
        task_map_generation="task-map-g1",
        call_result_status="invalid",
        issues=issues,
        source_event_id="evt-result-1",
    )
    assert first_payload["redispatch_allowed"] is True
    first = ZfEvent(
        id="evt-failure-1",
        type="fanout.child.failed",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "contract_revision": "contract-r1",
            "task_map_generation": "task-map-g1",
            **first_payload,
        },
    )
    second_payload = writer_call_result_failure_payload(
        [first],
        task_id="TASK-1",
        contract_revision="contract-r1",
        task_map_generation="task-map-g1",
        call_result_status="invalid",
        issues=issues,
        source_event_id="evt-result-2",
    )
    assert second_payload["no_progress"] is True
    assert second_payload["redispatch_allowed"] is False
    second = ZfEvent(
        id="evt-failure-2",
        type="fanout.child.failed",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "contract_revision": "contract-r1",
            "task_map_generation": "task-map-g1",
            **second_payload,
        },
    )
    block = writer_redispatch_block(
        [first, second],
        task_id="TASK-1",
        contract_revision="contract-r1",
        task_map_generation="task-map-g1",
    )
    assert block["dispatch_suppressed"] is True
    assert block["handoff_failure_count"] == 2
    assert writer_redispatch_block(
        [first, second],
        task_id="TASK-1",
        contract_revision="contract-r2",
        task_map_generation="task-map-g2",
    ) == {}


def test_canonical_plan_handoff_never_redispatches_same_contract() -> None:
    payload = writer_call_result_failure_payload(
        [],
        task_id="TASK-1",
        contract_revision="contract-r1",
        task_map_generation="task-map-g1",
        call_result_status="invalid",
        issues=[{
            "field": "control_result.contract_snapshot_ref",
            "code": "canonical_plan_command_unknown",
            "message": "acceptance references unknown command ids",
            "failure_owner": "canonical_plan",
            "recovery_owner": "planner",
            "recovery_action": "return_to_plan",
        }],
        source_event_id="evt-result-1",
    )
    assert payload["recovery_action"] == "return_to_plan"
    assert payload["redispatch_allowed"] is False
