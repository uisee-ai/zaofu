from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.runtime.call_result_adapters import AdaptedControlResult
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package,
    write_plan_artifact_package,
)
from zf.runtime.product_acceptance import (
    ProductAcceptanceError,
    bind_product_acceptance_report,
    product_acceptance_binding_from_package,
    provider_qualification_status,
    validate_product_acceptance_report,
    validate_product_acceptance_spec,
    validate_provider_qualification_receipt,
)
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot


RUN_ID = "workflow-product-1"
GENERATION = "generation-product-1"
TARGET = "c" * 40
CANDIDATE = "candidate/product-1"


def _spec(*, provider_required: bool = False) -> dict:
    return {
        "schema_version": "product_acceptance_spec.v1",
        "workflow_run_id": RUN_ID,
        "flow_kind": "prd",
        "plan_revision": "plan-r1",
        "task_map_generation": GENERATION,
        "assembly_owner": "TASK-ASSEMBLY",
        "entrypoints": [{
            "entrypoint_id": "web",
            "start_command": "npm run dev",
            "health_check": "curl -fsS http://127.0.0.1:3000/health",
            "owner": "TASK-ASSEMBLY",
        }],
        "user_journeys": [{
            "journey_id": "journey-create",
            "title": "Create one item",
            "mandatory": True,
            "verification_tier": "e2e",
            "assertions": [
                {
                    "assertion_id": "dom-result",
                    "statement": "The result is visible",
                    "observation_kind": "dom",
                    "mandatory": True,
                },
                {
                    "assertion_id": "visual-result",
                    "statement": "The rendered surface is non-empty",
                    "observation_kind": "visual",
                    "mandatory": True,
                },
            ],
        }],
        "provider_qualification": {
            "required_for_goal": provider_required,
            "providers": ["model-api"] if provider_required else [],
            "ttl_seconds": 300 if provider_required else 0,
        },
    }


def _receipt(*, status: str = "passed", expired: bool = False) -> dict:
    observed = datetime.now(timezone.utc) - timedelta(minutes=2)
    expires = observed - timedelta(seconds=1) if expired else observed + timedelta(minutes=5)
    return {
        "schema_version": "provider-qualification-receipt.v1",
        "provider": "model-api",
        "workflow_run_id": RUN_ID,
        "target_commit": TARGET,
        "status": status,
        "deterministic": False,
        "reusable": False,
        "observed_at": observed.isoformat(),
        "expires_at": expires.isoformat(),
        "evidence_refs": [{"ref": "artifacts/provider/output.json", "sha256": "a" * 64}],
    }


def _report(
    spec_descriptor: dict,
    *,
    include_visual: bool = True,
    receipts: list[dict] | None = None,
) -> dict:
    assertion_results = [{
        "assertion_id": "dom-result",
        "status": "passed",
        "evidence_refs": [{"ref": "artifacts/e2e/dom.json", "sha256": "b" * 64}],
    }]
    if include_visual:
        assertion_results.append({
            "assertion_id": "visual-result",
            "status": "passed",
            "evidence_refs": [{"ref": "artifacts/e2e/page.png", "sha256": "d" * 64}],
        })
    return {
        "schema_version": "product_acceptance_report.v1",
        "workflow_run_id": RUN_ID,
        "task_map_generation": GENERATION,
        "candidate_ref": CANDIDATE,
        "target_commit": TARGET,
        "product_acceptance_spec_ref": spec_descriptor["ref"],
        "product_acceptance_spec_digest": spec_descriptor["sha256"],
        "verdict": "passed",
        "journey_results": [{
            "journey_id": "journey-create",
            "status": "passed",
            "evidence_refs": [{"ref": "artifacts/e2e/report.json", "sha256": "e" * 64}],
            "assertion_results": assertion_results,
        }],
        "provider_qualification_receipts": list(receipts or []),
    }


def _package(state_dir: Path, *, provider_required: bool = False) -> tuple[dict, dict]:
    spec = _spec(provider_required=provider_required)
    spec_descriptor = write_immutable_json_sidecar(
        state_dir,
        spec,
        root="product-acceptance/specs",
        kind="product_acceptance_spec",
        schema_version="product_acceptance_spec.v1",
        created_by="test",
    )
    ports = []
    for name in ("requirement_spec", "goal_claim_set", "task_map", "planning_result"):
        descriptor = write_immutable_json_sidecar(
            state_dir,
            {"schema_version": f"{name}.v1", "value": name},
            root=f"fixtures/{name}",
            kind=name,
            schema_version=f"{name}.v1",
            created_by="test",
        )
        ports.append({
            "logical_name": name,
            "artifact_kind": name,
            "schema_version": f"{name}.v1",
            "producer_stage_id": "prd-plan",
            "ref": descriptor["ref"],
            "sha256": descriptor["sha256"],
        })
    ports.append({
        "logical_name": "product_acceptance_spec",
        "artifact_kind": "product_acceptance_spec",
        "schema_version": "product_acceptance_spec.v1",
        "producer_stage_id": "prd-plan",
        "ref": spec_descriptor["ref"],
        "sha256": spec_descriptor["sha256"],
    })
    run_contract = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    }
    run_contract["contract_digest"] = stable_json_sha256(run_contract)
    package = build_plan_artifact_package(
        workflow_run_id=RUN_ID,
        flow_kind="prd",
        producer_stage_id="prd-plan",
        run_contract=write_run_contract_snapshot(state_dir, run_contract),
        plan_revision="plan-r1",
        task_map_generation=GENERATION,
        produced=ports,
        required_ports=[
            "requirement_spec",
            "goal_claim_set",
            "task_map",
            "planning_result",
            "product_acceptance_spec",
        ],
    )
    return write_plan_artifact_package(state_dir, package), spec_descriptor


def _adapted(report: dict) -> AdaptedControlResult:
    return AdaptedControlResult(
        adapter_id="verification-result-v1-explicit",
        schema_version="verification-result.v1",
        payload={
            "schema_version": "verification-result.v1",
            "execution_status": "completed",
            "verdict": "passed",
            "verification_owner": "candidate_verify",
            "workflow_run_id": RUN_ID,
            "task_map_generation": GENERATION,
            "candidate_ref": CANDIDATE,
            "target_commit": TARGET,
            "product_acceptance_report": report,
        },
        descriptor={"ref": "unused", "sha256": "f" * 64},
    )


def test_spec_and_report_require_exact_mandatory_journey_evidence() -> None:
    spec = _spec()
    validate_product_acceptance_spec(spec)
    descriptor = {"ref": "artifacts/spec.json", "sha256": "1" * 64}
    report = _report(descriptor, include_visual=False)

    with pytest.raises(ProductAcceptanceError, match="mandatory assertions"):
        validate_product_acceptance_report(
            report,
            spec=spec,
            expected={
                "workflow_run_id": RUN_ID,
                "task_map_generation": GENERATION,
                "candidate_ref": CANDIDATE,
                "target_commit": TARGET,
                "product_acceptance_spec_ref": descriptor["ref"],
                "product_acceptance_spec_digest": descriptor["sha256"],
            },
        )


def test_provider_receipt_is_non_reusable_and_time_bounded() -> None:
    validate_provider_qualification_receipt(_receipt())
    reusable = {**_receipt(), "reusable": True}
    with pytest.raises(ProductAcceptanceError, match="reusable=false"):
        validate_provider_qualification_receipt(reusable)
    with pytest.raises(ProductAcceptanceError, match="expires_at must follow"):
        validate_provider_qualification_receipt(_receipt(expired=True))
    with pytest.raises(ProductAcceptanceError, match="exceeds policy"):
        validate_provider_qualification_receipt(_receipt(), ttl_seconds=30)


def test_passed_journey_cannot_hide_a_failed_assertion() -> None:
    spec = _spec()
    descriptor = {"ref": "artifacts/spec.json", "sha256": "1" * 64}
    report = _report(descriptor)
    report["journey_results"][0]["assertion_results"][0]["status"] = "failed"

    with pytest.raises(ProductAcceptanceError, match="passed with failed assertions"):
        validate_product_acceptance_report(
            report,
            spec=spec,
            expected={
                "workflow_run_id": RUN_ID,
                "task_map_generation": GENERATION,
                "candidate_ref": CANDIDATE,
                "target_commit": TARGET,
                "product_acceptance_spec_ref": descriptor["ref"],
                "product_acceptance_spec_digest": descriptor["sha256"],
            },
        )


def test_candidate_verify_binding_materializes_report_and_receipt(tmp_path: Path) -> None:
    package, spec_descriptor = _package(tmp_path, provider_required=True)
    operation = {
        "output_profile_id": "candidate-verify",
        "result_identity": {
            "workflow_run_id": RUN_ID,
            "task_map_generation": GENERATION,
            "candidate_ref": CANDIDATE,
            "target_commit": TARGET,
            "plan_artifact_package_ref": package["ref"],
            "plan_artifact_package_digest": package["sha256"],
        },
    }

    bound = bind_product_acceptance_report(
        _adapted(_report(spec_descriptor, receipts=[_receipt()])),
        state_dir=tmp_path,
        operation=operation,
        source_event_id="verify-event",
    )

    assert bound.issues == ()
    assert "product_acceptance_report" not in bound.payload
    assert bound.payload["product_acceptance_verdict"] == "passed"
    assert bound.payload["provider_qualification_status"] == "passed"
    assert bound.payload["product_acceptance_report_ref"].startswith(
        "artifacts/product-acceptance/reports/"
    )
    assert len(bound.payload["provider_qualification_receipt_refs"]) == 1


@pytest.mark.parametrize("verification_owner", ["task_verify", "candidate_verify"])
def test_candidate_verify_rejects_stale_target_and_missing_report(
    tmp_path: Path, verification_owner: str,
) -> None:
    package, spec_descriptor = _package(tmp_path)
    operation = {
        "output_profile_id": "candidate-verify",
        "result_identity": {
            "workflow_run_id": RUN_ID,
            "task_map_generation": GENERATION,
            "candidate_ref": CANDIDATE,
            "target_commit": TARGET,
            "plan_artifact_package_ref": package["ref"],
            "plan_artifact_package_digest": package["sha256"],
        },
    }
    stale = _report(spec_descriptor)
    stale["target_commit"] = "d" * 40
    stale_payload = _adapted(stale)
    stale_payload.payload["verification_owner"] = verification_owner

    stale_result = bind_product_acceptance_report(
        stale_payload,
        state_dir=tmp_path,
        operation=operation,
        source_event_id="verify-event-stale",
    )
    missing_payload = _adapted(_report(spec_descriptor))
    missing_payload.payload["verification_owner"] = verification_owner
    missing_payload.payload.pop("product_acceptance_report")
    missing_result = bind_product_acceptance_report(
        missing_payload,
        state_dir=tmp_path,
        operation=operation,
        source_event_id="verify-event-missing",
    )

    assert stale_result.issues[-1]["code"] == "product_acceptance_invalid"
    assert "target_commit mismatch" in stale_result.issues[-1]["message"]
    assert missing_result.issues[-1]["code"] == "product_acceptance_invalid"


def test_provider_failure_is_external_wait_not_product_rejection(tmp_path: Path) -> None:
    package, spec_descriptor = _package(tmp_path, provider_required=True)
    binding = product_acceptance_binding_from_package(tmp_path, package)
    report = _report(spec_descriptor, receipts=[_receipt(status="failed")])
    receipt_descriptor = write_immutable_json_sidecar(
        tmp_path,
        report.pop("provider_qualification_receipts")[0],
        root="provider-qualification/receipts",
        kind="provider_qualification_receipt",
        schema_version="provider-qualification-receipt.v1",
        created_by="test",
    )
    report["provider_qualification_receipt_refs"] = [receipt_descriptor]

    status = provider_qualification_status(
        tmp_path,
        spec=binding["spec"],
        report=report,
    )

    assert report["verdict"] == "passed"
    assert status == {
        "required": True,
        "status": "waiting_external",
        "providers": ["model-api"],
    }
