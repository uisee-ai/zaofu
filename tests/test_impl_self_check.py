from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.task.schema import Task, TaskContract
from zf.runtime.impl_self_check import (
    ImplSelfCheckError,
    completion_payload_template,
    hydrate_impl_self_check,
    normalize_impl_self_check,
    reusable_command_receipts,
    write_impl_self_check,
)
from zf.runtime.task_contract_snapshot import (
    build_target_snapshot,
    build_task_contract_snapshot,
    task_map_generation,
    write_task_contract_snapshot,
)
from zf.runtime.verification_commands import (
    command_digest,
    normalize_verification_commands,
)
from zf.runtime.verification_result import (
    VerificationResultError,
    normalize_verification_result,
)


def _snapshots(tmp_path: Path) -> tuple[dict, dict]:
    task = Task(
        id="TASK-148",
        title="Deliver structured verification",
        contract=TaskContract(
            behavior="deliver",
            verification="pytest -q tests/test_one.py",
            validation={
                "commands": [
                    {
                        "id": "unit-focused",
                        "command": "pytest -q tests/test_one.py",
                        "acceptance_ids": ["AC-1"],
                        "owner": "impl_self_check",
                        "tier": "task_non_smoke",
                        "deterministic": True,
                        "reusable": True,
                        "timeout_seconds": 180,
                    }
                ]
            },
            verification_tiers=["task_non_smoke"],
            scope=["src/**", "tests/**"],
            plan_ref="artifacts/plan/task-map.json",
            acceptance_criteria=[{
                "id": "AC-1",
                "text": "The focused behavior passes",
                "verification_owner": "task_verify",
                "verification_tier": "task_non_smoke",
                "verification_command_ids": ["unit-focused"],
            }],
        ),
    )
    contract = build_task_contract_snapshot(
        task,
        workflow_run_id="run-148",
        task_map_generation_id=task_map_generation(task),
        base_commit="a" * 40,
        task_ref="refs/zf/tasks/TASK-148",
    )
    descriptor = write_task_contract_snapshot(tmp_path, contract)
    target = build_target_snapshot(
        descriptor,
        target_commit="c" * 40,
        contract_snapshot=contract,
    )
    target.update({
        "target_snapshot_ref": "artifacts/target.json",
        "target_snapshot_digest": "d" * 64,
    })
    return contract, target


def _payload(contract: dict, target: dict) -> dict:
    command = contract["verification_commands"][0]
    return {
        "attempt_id": "attempt-1",
        "impl_self_check": {
            "schema_version": "impl-self-check.v1",
            "workflow_run_id": contract["workflow_run_id"],
            "task_id": contract["task_id"],
            "attempt_id": "attempt-1",
            "contract_revision": contract["contract_revision"],
            "task_map_generation": contract["task_map_generation"],
            "source_commit": target["target_commit"],
            "target_commit": target["target_commit"],
            "contract_snapshot_ref": target["contract_snapshot_ref"],
            "contract_snapshot_digest": target["contract_snapshot_digest"],
            "command_receipts": [{
                "receipt_id": "receipt-unit",
                "command_id": command["command_id"],
                "command_digest": command["command_digest"],
                "target_commit": target["target_commit"],
                "status": "pass",
                "exit_code": 0,
                "evidence_refs": ["artifacts/unit.log"],
            }],
            "acceptance_results": [{
                "acceptance_id": "AC-1",
                "status": "pass",
                "command_receipt_ids": ["receipt-unit"],
                "evidence_refs": ["artifacts/ac-1.json"],
                "residual_risks": [],
            }],
            "residual_risks": [],
            "evidence_refs": ["artifacts/impl-summary.json"],
        },
    }


def test_verification_commands_remain_independent_and_identified() -> None:
    commands = normalize_verification_commands([
        "pytest -q tests/test_a.py",
        "pytest -q tests/test_b.py",
    ])

    assert [item["id"] for item in commands] == [
        "contract-verification-1",
        "contract-verification-2",
    ]
    assert [item["command"] for item in commands] == [
        "pytest -q tests/test_a.py",
        "pytest -q tests/test_b.py",
    ]
    assert commands[0]["command_digest"] == command_digest(commands[0]["command"])


def test_verification_commands_preserve_explicit_rolling_smoke_marker() -> None:
    commands = normalize_verification_commands([{
        "id": "focused-tests",
        "command": "pytest -q tests/test_a.py",
        "tier": "runtime",
        "rolling_smoke": True,
    }])

    assert commands[0]["rolling_smoke"] is True


def test_verification_commands_preserve_declared_producer_paths() -> None:
    commands = normalize_verification_commands([{
        "id": "browser-e2e",
        "command": "npm run test:e2e",
        "acceptance_ids": ["AC-10"],
        "producer_paths": ["artifacts/e2e/results.json"],
    }])

    assert commands[0]["producer_paths"] == ["artifacts/e2e/results.json"]


def test_verification_commands_preserve_declared_producer_task() -> None:
    commands = normalize_verification_commands([{
        "id": "browser-e2e",
        "command": "npm run test:e2e",
        "producer_task_id": "TASK-EVIDENCE",
    }])

    assert commands[0]["producer_task_id"] == "TASK-EVIDENCE"


def test_impl_self_check_round_trip_and_exact_target_reuse(tmp_path: Path) -> None:
    contract, target = _snapshots(tmp_path)
    body = normalize_impl_self_check(
        _payload(contract, target),
        contract_snapshot=contract,
        target_snapshot=target,
        expected_attempt_id="attempt-1",
    )
    descriptor = write_impl_self_check(
        tmp_path,
        body,
        source_event_id="evt-build",
        created_by="dev-1",
    )
    hydrated = hydrate_impl_self_check(
        tmp_path,
        descriptor,
        contract_snapshot=contract,
        target_snapshot=target,
    )

    assert hydrated["acceptance_results"][0]["status"] == "passed"
    assert [item["receipt_id"] for item in reusable_command_receipts(
        hydrated,
        contract_snapshot=contract,
        target_snapshot=target,
    )] == ["receipt-unit"]
    changed_target = {**target, "target_commit": "e" * 40}
    assert reusable_command_receipts(
        hydrated,
        contract_snapshot=contract,
        target_snapshot=changed_target,
    ) == []


def test_impl_self_check_requires_current_stage_command_subset(
    tmp_path: Path,
) -> None:
    contract, target = _snapshots(tmp_path)
    candidate_command = "npm run test:e2e"
    contract["verification_commands"].append({
        "command_id": "candidate-e2e",
        "command": candidate_command,
        "command_digest": command_digest(candidate_command),
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

    body = normalize_impl_self_check(
        _payload(contract, target),
        contract_snapshot=contract,
        target_snapshot=target,
        expected_attempt_id="attempt-1",
    )
    template = completion_payload_template(
        contract_snapshot=contract,
        task_item={
            "attempt_id": "attempt-1",
            "contract_snapshot_ref": target["contract_snapshot_ref"],
            "contract_snapshot_digest": target["contract_snapshot_digest"],
        },
        task_id=contract["task_id"],
        run_id=contract["workflow_run_id"],
        child_id="child-1",
    )

    assert [
        item["command_id"] for item in body["command_receipts"]
    ] == ["unit-focused"]
    assert [
        item["command_id"]
        for item in template["impl_self_check"]["command_receipts"]
    ] == ["unit-focused"]
    assert template["impl_self_check"]["acceptance_results"][0][
        "command_receipt_ids"
    ] == ["receipt-unit-focused"]


def test_impl_self_check_defers_command_to_declared_producer_task(
    tmp_path: Path,
) -> None:
    contract, target = _snapshots(tmp_path)
    contract["verification_commands"][0]["producer_task_id"] = "TASK-EVIDENCE"
    payload = _payload(contract, target)
    payload["impl_self_check"]["command_receipts"] = []
    payload["impl_self_check"]["acceptance_results"][0][
        "command_receipt_ids"
    ] = []

    body = normalize_impl_self_check(
        payload,
        contract_snapshot=contract,
        target_snapshot=target,
        expected_attempt_id="attempt-1",
    )
    template = completion_payload_template(
        contract_snapshot=contract,
        task_item={
            "attempt_id": "attempt-1",
            "contract_snapshot_ref": target["contract_snapshot_ref"],
            "contract_snapshot_digest": target["contract_snapshot_digest"],
        },
        task_id=contract["task_id"],
        run_id=contract["workflow_run_id"],
        child_id="child-1",
    )

    assert body["command_receipts"] == []
    assert template["impl_self_check"]["command_receipts"] == []


@pytest.mark.parametrize("owner", ["impl_self_check", "task_verify"])
@pytest.mark.parametrize(
    "mutation",
    ["missing_ac", "failed_ac", "missing_receipt", "failed_receipt", "bad_digest", "bad_target"],
)
def test_impl_self_check_rejects_incomplete_or_stale_evidence(
    tmp_path: Path,
    mutation: str,
    owner: str,
) -> None:
    contract, target = _snapshots(tmp_path)
    contract["verification_commands"][0]["owner"] = owner
    payload = _payload(contract, target)
    body = payload["impl_self_check"]
    if mutation == "missing_ac":
        body["acceptance_results"] = []
    elif mutation == "failed_ac":
        body["acceptance_results"][0]["status"] = "failed"
    elif mutation == "missing_receipt":
        body["command_receipts"] = []
        body["acceptance_results"][0]["command_receipt_ids"] = []
    elif mutation == "failed_receipt":
        body["command_receipts"][0]["status"] = "failed"
        body["command_receipts"][0]["exit_code"] = 1
    elif mutation == "bad_digest":
        body["command_receipts"][0]["command_digest"] = "0" * 64
    else:
        body["command_receipts"][0]["target_commit"] = "e" * 40

    with pytest.raises(ImplSelfCheckError):
        normalize_impl_self_check(
            payload,
            contract_snapshot=contract,
            target_snapshot=target,
            expected_attempt_id="attempt-1",
        )


def test_impl_self_check_binds_omitted_receipt_identity(
    tmp_path: Path,
) -> None:
    contract, target = _snapshots(tmp_path)
    payload = _payload(contract, target)
    receipt = payload["impl_self_check"]["command_receipts"][0]
    receipt.pop("command_digest")
    receipt.pop("target_commit")

    body = normalize_impl_self_check(
        payload,
        contract_snapshot=contract,
        target_snapshot=target,
        expected_attempt_id="attempt-1",
    )

    normalized = body["command_receipts"][0]
    assert normalized["command_digest"] == (
        contract["verification_commands"][0]["command_digest"]
    )
    assert normalized["target_commit"] == target["target_commit"]


def test_strict_rejection_requires_exact_rework_items(tmp_path: Path) -> None:
    contract, target = _snapshots(tmp_path)
    result = {
        "verification_result": {
            "execution_status": "completed",
            "verdict": "rejected",
            "requirement_results": [{
                "acceptance_id": "AC-1",
                "status": "failed",
                "verification_owner": "task_verify",
                "verification_tier": "task_non_smoke",
                "findings": [{"message": "returned zero"}],
                "evidence_refs": ["artifacts/failure.log"],
            }],
        }
    }
    with pytest.raises(VerificationResultError, match="rework_items"):
        normalize_verification_result(
            result,
            contract_snapshot=contract,
            target_snapshot=target,
            require_rework_items=True,
        )

    result["verification_result"]["rework_items"] = [{
        "rework_item_id": "RW-AC-1",
        "status": "incorrect",
        "acceptance_id": "AC-1",
        "expected": "returns one",
        "observed": "returned zero",
        "required_delta": "return one on the accepted branch",
        "reproduction_command_ids": ["unit-focused"],
        "allowed_scope": ["src/**"],
        "done_when": "unit-focused passes on the repaired target",
        "next_gate": "task_verify",
        "owner": "implementation_owner",
    }]
    normalized = normalize_verification_result(
        result,
        contract_snapshot=contract,
        target_snapshot=target,
        require_rework_items=True,
    )
    assert normalized["rework_items"][0]["required_delta"].startswith("return one")
