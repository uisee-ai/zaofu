from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_call_result_runtime import _runtime
from tests.test_product_acceptance import (
    CANDIDATE,
    GENERATION,
    RUN_ID,
    TARGET,
    _adapted,
    _package,
    _report,
)
from zf.core.config.schema import (
    FanoutAggregateConfig,
    FanoutAssignmentConfig,
    WorkflowStageConfig,
)
from zf.runtime.call_result_runtime import prepare_call_operation
from zf.runtime.product_acceptance import bind_product_acceptance_report
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.mark.parametrize("discovery_wired", [False, True])
def test_eight_candidate_task_operations_require_a_wired_global_consumer(
    tmp_path: Path, discovery_wired: bool,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.config.workflow.flow_metadata.update({
        "flow_kind": "prd",
        "post_verify_discovery": "product_completeness",
    })
    runtime.config.workflow.stages = [
        WorkflowStageConfig(
            id="prd-lanes-verify",
            flow_kind="prd",
            trigger="candidate.ready",
            topology="fanout_reader",
            assignment=FanoutAssignmentConfig(
                strategy="affinity_stage_slots", stage_slot="verify",
            ),
        ),
    ]
    if discovery_wired:
        runtime.config.workflow.stages.append(WorkflowStageConfig(
            id="prd-post-verify-discovery",
            trigger="flow.discovery.requested",
            topology="fanout_reader",
            roles=["flow-discovery"],
            aggregate=FanoutAggregateConfig(
                child_success_event="flow.discovery.child.completed",
                success_event="flow.discovery.completed",
            ),
        ))
    operations = []
    for index in range(8):
        task_id = f"T{index}"
        payload = {
            "workflow_run_id": "run-scoped",
            "flow_kind": "prd",
            "task_id": task_id,
            "upstream_task_id": task_id,
            "assignment_strategy": "affinity_stage_slots",
            "stage_slot": "verify",
            "candidate_head_commit": TARGET,
            "target_commit": TARGET,
            "role_instance": f"verify-lane-{index}",
        }
        prepared = prepare_call_operation(
            runtime, payload=payload,
            operation_type="fanout_reader_child",
            operation_key=task_id, stage_id="prd-lanes-verify",
            task_id=task_id, dispatch_id=f"dispatch-{task_id}",
        )
        operations.append(prepared.operation_id)
        assert prepared.output_profile_id == (
            "task-verify" if discovery_wired else "candidate-verify"
        )
        assert payload["target_commit"] == TARGET
        assert payload["task_id"] == task_id
    assert len(set(operations)) == 8


def _discovery_operation(package: dict) -> dict:
    return {
        "output_profile_id": "workflow-read",
        "canonical_success_event": "flow.discovery.child.completed",
        "result_identity": {
            "workflow_run_id": RUN_ID,
            "task_map_generation": GENERATION,
            "candidate_ref": CANDIDATE,
            "target_commit": TARGET,
            "plan_artifact_package_ref": package["ref"],
            "plan_artifact_package_digest": package["sha256"],
        },
    }


def _discovery_result(report: dict):
    source = _adapted(report)
    payload = dict(source.payload)
    payload.pop("verification_owner")
    payload["schema_version"] = "workflow-read-result.v1"
    return replace(
        source, adapter_id="workflow-read-v1",
        schema_version="workflow-read-result.v1", payload=payload,
    )


@pytest.mark.parametrize("defect", ["missing_report", "missing_assertion", "wrong_target"])
def test_global_discovery_cannot_close_partial_or_stale_product_evidence(
    tmp_path: Path, defect: str,
) -> None:
    package, spec = _package(tmp_path)
    result = _discovery_result(_report(spec, include_visual=defect != "missing_assertion"))
    if defect == "missing_report":
        result.payload.pop("product_acceptance_report")
    elif defect == "wrong_target":
        result.payload["product_acceptance_report"]["target_commit"] = "d" * 40
    bound = bind_product_acceptance_report(
        result, state_dir=tmp_path, operation=_discovery_operation(package),
        source_event_id="discovery-result",
    )
    assert bound.issues
    assert bound.issues[-1]["code"] == "product_acceptance_invalid"


def test_global_discovery_materializes_complete_report_without_changing_result_schema(
    tmp_path: Path,
) -> None:
    package, spec = _package(tmp_path)
    bound = bind_product_acceptance_report(
        _discovery_result(_report(spec)),
        state_dir=tmp_path, operation=_discovery_operation(package),
        source_event_id="discovery-result",
    )
    assert not bound.issues
    assert bound.payload["product_acceptance_verdict"] == "passed"
    assert bound.schema_version == "workflow-read-result.v1"
    stored = hydrate_sidecar_ref(tmp_path, bound.descriptor).payload
    assert stored["schema_version"] == bound.schema_version
    assert "product_acceptance_report" not in stored
    assert stored["product_acceptance_report_digest"]
    replayed = bind_product_acceptance_report(
        bound, state_dir=tmp_path, operation=_discovery_operation(package),
        source_event_id="discovery-result",
    )
    assert not replayed.issues
    assert replayed.payload["product_acceptance_report_digest"] == stored["product_acceptance_report_digest"]


def test_ordinary_scan_is_not_promoted_to_product_acceptance(tmp_path: Path) -> None:
    package, spec = _package(tmp_path)
    operation = _discovery_operation(package)
    operation["canonical_success_event"] = "prd.scan.child.completed"
    result = _discovery_result(_report(spec))
    result.payload.pop("product_acceptance_report")
    bound = bind_product_acceptance_report(
        result, state_dir=tmp_path, operation=operation, source_event_id="scan-result",
    )
    assert bound is result


@pytest.mark.parametrize("claim_future_pass", [False, True])
def test_candidate_only_contract_keeps_future_acceptance_unclaimed(
    tmp_path: Path, claim_future_pass: bool,
) -> None:
    from tests.test_typed_result_admission import _contract, _verification_result
    from zf.runtime.task_contract_snapshot import (
        build_target_snapshot, write_target_snapshot, write_task_contract_snapshot,
    )
    from zf.runtime.typed_result_admission import typed_result_admission_issues

    contract = _contract()
    contract["verification_owner"] = "task_verify"
    contract["acceptance_criteria"][0]["verification_owner"] = "candidate_verify"
    contract["verification_commands"][0]["owner"] = "candidate_verify"
    descriptor = write_task_contract_snapshot(tmp_path, contract)
    target = write_target_snapshot(tmp_path, build_target_snapshot(
        descriptor, target_commit="target-1", contract_snapshot=contract,
    ))
    result = _verification_result(contract, descriptor, target)
    result["probe_receipts"] = [{
        "probe_id": "candidate-identity-read",
        "status": "passed",
        "target_commit": "target-1",
        "evidence_refs": ["artifact:candidate-identity-read"],
    }]
    row = result["requirement_results"][0]
    row.update({
        "verification_owner": "candidate_verify",
        "status": "passed" if claim_future_pass else "not_applicable",
        "findings": ["Deferred to the configured full-product consumer."],
    })
    issues = typed_result_admission_issues(
        tmp_path, schema_version="verification-result.v1",
        result=result, semantic_submit=True,
    )
    assert bool(issues) is claim_future_pass
