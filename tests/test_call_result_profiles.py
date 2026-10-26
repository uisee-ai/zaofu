from __future__ import annotations

from pathlib import Path

import pytest

from zf.runtime.call_result_adapters import (
    ControlResultAdapterError,
    ControlResultAdapterRegistry,
    call_result_profile_identity,
    hydrate_profiled_control_result_event,
)
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_delivery_result import artifact_delivery_success_payload
from zf.runtime.call_result_envelope import (
    normalize_call_result_envelope,
    write_immutable_json_sidecar,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def test_profile_identity_distinguishes_verify_surfaces() -> None:
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="task-verify",
        payload={},
    ) == ("task-verify", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="candidate-verify",
        payload={},
    ) == ("candidate-verify", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="prd-lanes-verify",
        payload={
            "task_id": "TASK-1",
            "candidate_head_commit": "candidate-head",
        },
    ) == ("candidate-verify", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="issue-post-verify-discovery",
        payload={
            "candidate_head_commit": "candidate-head",
            "task_map_generation": "generation-1",
            "canonical_success_event": "flow.discovery.child.completed",
        },
    ) == ("workflow-read", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="flow-module-parity-scan",
        payload={
            "candidate_head_commit": "candidate-head",
            "canonical_success_event": "module.parity.child.completed",
        },
    ) == ("workflow-read", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="flow-verify-bridge",
        payload={
            "candidate_head_commit": "candidate-head",
            "canonical_success_event": "verify.bridge.child.completed",
        },
    ) == ("workflow-read", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="global-rescan",
        payload={},
    ) == ("global-rescan", "1")
    assert call_result_profile_identity(
        operation_type="fanout_synth",
        stage_id="plan",
        payload={},
    ) == ("plan-synth", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="flow-scan",
        payload={"canonical_success_event": "workflow.child.completed"},
    ) == ("workflow-read", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="issue-triage",
        payload={"canonical_success_event": "issue.triage.child.completed"},
    ) == ("workflow-read", "1")


def test_workflow_read_profile_preserves_complete_report(tmp_path: Path) -> None:
    event, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        tmp_path,
        profile_id="workflow-read",
        revision="1",
        event_type="workflow.child.completed",
        semantic_result={
            "status": "passed",
            "summary": "scan complete",
            "recommendation": "approve",
            "coverage_matrix": [{"scope": "simulation", "status": "covered"}],
            "findings": [],
        },
        identity={"workflow_run_id": "run-read"},
        source_event_id="evt-read",
        actor="scan-runtime",
        task_id="",
        correlation_id="run-read",
    )

    assert event.payload["report"]["coverage_matrix"][0]["scope"] == "simulation"
    assert adapted.payload["verdict"] == "passed"
    assert adapted.payload["summary"] == "scan complete"


def test_workflow_read_profile_preserves_top_level_plan_handoff(
    tmp_path: Path,
) -> None:
    plan_ports = [{
        "logical_name": "test_matrix",
        "schema_version": "test-matrix.v1",
        "body": {
            "schema_version": "test-matrix.v1",
            "status": "ready",
            "metadata": {
                "enrichment_contract": {"status": "fulfilled"},
            },
            "tests": [{"id": "TEST-FULL-SUITE", "command": "npm test"}],
        },
    }]
    adapted = ControlResultAdapterRegistry().adapt(
        tmp_path,
        ZfEvent(
            type="prd.plan.child.completed",
            payload={
                "output_profile_id": "workflow-read",
                "canonical_success_event": "prd.plan.child.completed",
                "canonical_failure_event": "prd.plan.child.failed",
                "plan_ports": plan_ports,
                "artifact_refs": ["artifacts/plan/test_matrix.json"],
                "task_map_ref": "artifacts/plan/task_map.json",
                "report": {
                    "status": "passed",
                    "summary": "plan ready",
                    "recommendation": "approve",
                    "findings": [],
                    "plan_ports": [],
                },
            },
        ),
    )

    assert adapted.payload["plan_ports"] == plan_ports
    assert adapted.payload["artifact_refs"] == [
        "artifacts/plan/test_matrix.json"
    ]
    assert adapted.payload["task_map_ref"] == "artifacts/plan/task_map.json"


def test_workflow_read_profile_accepts_registered_product_child_event(
    tmp_path: Path,
) -> None:
    event, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        tmp_path,
        profile_id="workflow-read",
        revision="1",
        event_type="issue.triage.child.completed",
        semantic_result={
            "status": "passed",
            "summary": "triage complete",
            "recommendation": "approve",
            "findings": [],
        },
        identity={"workflow_run_id": "run-issue"},
        source_event_id="evt-issue",
        actor="issue-triage",
        task_id="ISSUE-ANCHOR",
        correlation_id="run-issue",
    )

    assert event.type == "issue.triage.child.completed"
    assert adapted.adapter_id == "workflow-read-result-v1"
    assert adapted.payload["verdict"] == "passed"


def test_product_flow_artifact_production_does_not_require_generic_output_ports(
    tmp_path: Path,
) -> None:
    _, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        tmp_path,
        profile_id="workflow-read",
        revision="1",
        event_type="workflow.child.completed",
        semantic_result={
            "status": "failed",
            "summary": "Report produced with gaps in the reviewed subject.",
            "recommendation": "needs_rework",
            "findings": [{"message": "The subject is missing one source."}],
        },
        identity={"result_semantics": "artifact_production"},
        source_event_id="evt-artifact-produced",
        actor="workflow-synthesizer",
        task_id="",
        correlation_id="run-workflow",
    )

    assert adapted.payload["execution_status"] == "completed"
    assert adapted.payload["verdict"] == "passed"
    assert adapted.payload["subject_verdict"] == "needs_rework"
    assert adapted.payload["result_semantics"] == "artifact_production"
    assert adapted.issues == ()
    assert "output_artifacts" not in adapted.payload


def test_top_level_failed_artifact_report_is_completed_with_subject_rework(
    tmp_path: Path,
) -> None:
    adapted = ControlResultAdapterRegistry().adapt(
        tmp_path,
        ZfEvent(
            type="scope.failed",
            payload={
                "output_profile_id": "workflow-read",
                "canonical_failure_event": "scope.failed",
                "result_semantics": "artifact_production",
                "status": "failed",
                "execution_status": "failed",
                "summary": "Audit completed; the reproduced product test fails.",
                "recommendation": "needs_rework",
                "findings": [{
                    "message": "node tests/repro-grid-parser.mjs exits 1",
                    "reproduction_command": "node tests/repro-grid-parser.mjs",
                }],
            },
        ),
    )

    assert adapted.payload["execution_status"] == "completed"
    assert adapted.payload["verdict"] == "passed"
    assert adapted.payload["subject_verdict"] == "needs_rework"
    assert adapted.payload["recommendation"] == "needs_rework"
    assert adapted.payload["findings"][0]["reproduction_command"].startswith(
        "node tests/"
    )
    assert adapted.issues == ()


def test_artifact_production_keeps_explicit_provider_failure_failed(
    tmp_path: Path,
) -> None:
    adapted = ControlResultAdapterRegistry().adapt(
        tmp_path,
        ZfEvent(
            type="scope.failed",
            payload={
                "output_profile_id": "workflow-read",
                "canonical_failure_event": "scope.failed",
                "result_semantics": "artifact_production",
                "execution_status": "failed",
                "failure_class": "provider_execution_failure",
                "provider_error": "provider process exited before a result",
                "summary": "Provider did not produce the assigned audit.",
                "findings": [],
            },
        ),
    )

    assert adapted.payload["execution_status"] == "failed"
    assert adapted.payload["verdict"] == "abstained"
    assert adapted.payload["failure_class"] == "reader_execution_failure"


def test_workflow_output_is_materialized_and_prefilled_for_artifact_verify(
    tmp_path: Path,
) -> None:
    event, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        tmp_path,
        profile_id="workflow-read",
        revision="1",
        event_type="workflow.child.completed",
        semantic_result={
            "status": "passed",
            "summary": "# Final report\n\nGrounded synthesis body.",
            "recommendation": "approve",
            "findings": [],
        },
        identity={
            "workflow_run_id": "run-output",
            "stage_id": "synthesize",
            "generic_workflow_operation": "agent.synthesize",
            "result_semantics": "artifact_production",
            "workflow_output_ports": [{
                "name": "report",
                "kind": "report/markdown",
            }],
        },
        source_event_id="evt-output",
        actor="workflow-synthesizer",
        task_id="",
        correlation_id="run-output",
    )

    assert adapted.issues == ()
    artifact = adapted.payload["output_artifacts"][0]
    assert artifact["source_ref"] == "synthesize.report"
    assert hydrate_sidecar_ref(tmp_path, artifact).payload.startswith(
        "# Final report"
    )
    envelope = normalize_call_result_envelope(
        source_payload={**event.payload, "attempt_id": "attempt-synthesize"},
        control_result={
            "schema_version": adapted.schema_version,
            **adapted.descriptor,
        },
        workflow_run_id="run-output",
        operation_id="operation-synthesize",
        request_hash="request-synthesize",
        source_event_id="evt-output",
        source_event_type=event.type,
        actor="workflow-synthesizer",
        correlation_id="run-output",
    )
    envelope_ref = write_immutable_json_sidecar(
        tmp_path,
        envelope,
        root="call-results/envelopes",
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="test",
    )
    template = artifact_delivery_success_payload(
        {
            "workflow_run_id": "run-output",
            "goal_id": "goal-output",
            "workflow_generation": "a" * 64,
            "request_revision": 1,
            "generic_workflow_contract_digest": "b" * 64,
            "workflow_intent": "research",
            "workflow_template": "evidence-synthesis-v1",
            "completion_profile": "artifact_delivery",
            "run_contract_ref": "artifacts/run-contracts/current.json",
            "run_contract_digest": "c" * 64,
            "goal_claim_set_ref": "artifacts/goal-claims/current.json",
            "goal_claim_set_digest": "d" * 64,
            "required_delivery_artifacts": [{
                "name": "report",
                "kind": "report/markdown",
                "source_ref": "synthesize.report",
            }],
            "input_result_refs": [envelope_ref["ref"]],
        },
        verifier_stage_id="verify",
        verifier_role="workflow-verifier",
        state_dir=tmp_path,
    )

    assert template["artifact_delivery_result"]["artifacts"] == [artifact]


def test_legacy_generic_stage_output_is_resolved_after_restart(
    tmp_path: Path,
) -> None:
    report = write_immutable_json_sidecar(
        tmp_path,
        {"title": "Legacy admitted report"},
        root="workflow/artifacts/reports",
        kind="report/markdown",
        schema_version="research-report.v1",
        created_by="workflow-synthesizer",
    )
    control = write_immutable_json_sidecar(
        tmp_path,
        {
            "schema_version": "generic-stage-result.v1",
            "stage_id": "synthesize",
            "status": "passed",
            "output_ref": report,
        },
        root="workflow/stage-results/synthesize",
        kind="generic_stage_result",
        schema_version="generic-stage-result.v1",
        created_by="workflow-synthesizer",
    )
    envelope = normalize_call_result_envelope(
        source_payload={
            "workflow_generation": "a" * 64,
            "attempt_id": "attempt-synthesize",
            "stage_id": "synthesize",
        },
        control_result={
            "schema_version": "generic-stage-result.v1",
            **control,
        },
        workflow_run_id="run-output",
        operation_id="operation-synthesize",
        request_hash="request-synthesize",
        source_event_id="evt-synthesize",
        source_event_type="synthesize.completed",
        actor="workflow-synthesizer",
        correlation_id="run-output",
    )
    envelope_ref = write_immutable_json_sidecar(
        tmp_path,
        envelope,
        root="call-results/envelopes",
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="test",
    )

    template = artifact_delivery_success_payload(
        {
            "workflow_run_id": "run-output",
            "goal_id": "goal-output",
            "workflow_generation": "a" * 64,
            "request_revision": 1,
            "generic_workflow_contract_digest": "b" * 64,
            "workflow_intent": "research",
            "workflow_template": "evidence-synthesis-v1",
            "completion_profile": "artifact_delivery",
            "run_contract_ref": "artifacts/run-contracts/current.json",
            "run_contract_digest": "c" * 64,
            "goal_claim_set_ref": "artifacts/goal-claims/current.json",
            "goal_claim_set_digest": "d" * 64,
            "required_delivery_artifacts": [{
                "name": "report",
                "kind": "report/markdown",
                "source_ref": "synthesize.report",
            }],
            "input_result_refs": [envelope_ref["ref"]],
        },
        verifier_stage_id="verify",
        verifier_role="workflow-verifier",
        state_dir=tmp_path,
    )

    artifact = template["artifact_delivery_result"]["artifacts"][0]
    assert artifact["ref"] == report["ref"]
    assert artifact["source_ref"] == "synthesize.report"
    assert artifact["producer_stage_id"] == "synthesize"


def test_invalid_artifact_delivery_keeps_kernel_pinned_verifier_identity(
    tmp_path: Path,
) -> None:
    adapted = ControlResultAdapterRegistry().adapt(
        tmp_path,
        ZfEvent(
            type="workflow.child.completed",
            payload={
                "stage_id": "verify",
                "role_instance": "workflow-verifier",
                "artifact_delivery_result": {
                    "schema_version": "artifact-delivery-result.v1",
                    "workflow_run_id": "run-output",
                    "goal_id": "goal-output",
                    "workflow_generation": "a" * 64,
                    "request_revision": 1,
                    "generic_workflow_contract_digest": "b" * 64,
                    "run_contract_ref": "artifacts/run-contracts/current.json",
                    "run_contract_digest": "c" * 64,
                    "completion_profile": "artifact_delivery",
                    "goal_claim_set_ref": "artifacts/goal-claims/current.json",
                    "goal_claim_set_digest": "d" * 64,
                    "verifier_stage_id": "",
                    "verifier_role": "",
                    "verdict": "failed",
                    "summary": "Verification found a gap.",
                },
            },
        ),
    )

    assert adapted.payload["verifier_stage_id"] == "verify"
    assert adapted.payload["verifier_role"] == "workflow-verifier"
    assert any(
        issue["code"] == "schema_invalid"
        and "invalid verdict 'failed'" in issue.get("message", "")
        for issue in adapted.issues
    )


def test_workflow_subject_gate_uses_subject_rework_as_control_rejection(
    tmp_path: Path,
) -> None:
    _, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        tmp_path,
        profile_id="workflow-read",
        revision="1",
        event_type="workflow.child.completed",
        semantic_result={
            "status": "failed",
            "summary": "Required source is absent.",
            "recommendation": "needs_rework",
            "findings": [{"message": "Add the required source."}],
        },
        identity={"result_semantics": "subject_gate"},
        source_event_id="evt-subject-gate",
        actor="workflow-verifier",
        task_id="",
        correlation_id="run-workflow",
    )

    assert adapted.payload["execution_status"] == "completed"
    assert adapted.payload["verdict"] == "rejected"
    assert adapted.payload["subject_verdict"] == "needs_rework"
    assert adapted.payload["failure_class"] == "semantic_rejection"


def test_artifact_delivery_profile_overrides_agent_copied_identity(
    tmp_path: Path,
) -> None:
    claim_ref = "artifacts/goal-closure/claim-sets/current.json"
    report_ref = "artifacts/workflow/reports/current.json"
    result_ref = "artifacts/call-results/envelopes/synthesize.json"
    _, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        tmp_path,
        profile_id="artifact-delivery",
        revision="1",
        event_type="workflow.child.completed",
        semantic_result={
            "schema_version": "artifact-delivery-result.v1",
            "workflow_run_id": "run-stale",
            "goal_id": "goal-stale",
            "workflow_generation": "d" * 64,
            "request_revision": 1,
            "generic_workflow_contract_digest": "e" * 64,
            "run_contract_ref": "artifacts/run-contracts/stale.json",
            "run_contract_digest": "f" * 64,
            "completion_profile": "artifact_delivery",
            "goal_claim_set_ref": "artifacts/goal-closure/stale.json",
            "goal_claim_set_digest": "0" * 64,
            "verifier_stage_id": "verify",
            "verifier_role": "workflow-verifier",
            "verdict": "passed",
            "artifacts": [{
                "name": "report",
                "kind": "report/markdown",
                "source_ref": "synthesize.report",
                "producer_stage_id": "synthesize",
                "ref": report_ref,
                "sha256": "1" * 64,
            }],
            "goal_coverage": [{
                "goal_claim_id": "AC-1",
                "status": "closed",
                "supporting_artifact_refs": [report_ref],
            }],
            "input_result_refs": [result_ref],
            "verification_evidence_refs": [report_ref],
            "open_gap_refs": [],
            "recommended_action": "complete",
            "summary": "Verified report closes the Goal.",
        },
        identity={
            "workflow_run_id": "run-current",
            "goal_id": "goal-current",
            "workflow_generation": "a" * 64,
            "request_revision": 2,
            "generic_workflow_contract_digest": "b" * 64,
            "run_contract_ref": "artifacts/run-contracts/current.json",
            "run_contract_digest": "c" * 64,
            "completion_profile": "artifact_delivery",
            "goal_claim_set_ref": claim_ref,
            "goal_claim_set_digest": "2" * 64,
        },
        source_event_id="evt-artifact-delivery",
        actor="workflow-verifier",
        task_id="",
        correlation_id="run-current",
    )

    assert adapted.payload["workflow_run_id"] == "run-current"
    assert adapted.payload["goal_id"] == "goal-current"
    assert adapted.payload["workflow_generation"] == "a" * 64
    assert adapted.payload["request_revision"] == 2
    assert adapted.payload["goal_claim_set_ref"] == claim_ref
    assert adapted.payload["goal_claim_set_digest"] == "2" * 64


def test_workflow_read_legacy_event_requires_pinned_product_event(
    tmp_path: Path,
) -> None:
    event = ZfEvent(
        type="issue.triage.child.completed",
        payload={
            "output_profile_id": "workflow-read",
            "canonical_success_event": "issue.triage.child.completed",
            "canonical_failure_event": "issue.triage.child.failed",
            "report": {
                "status": "passed",
                "summary": "triage complete",
                "recommendation": "approve",
                "findings": [],
            },
        },
    )

    adapted = ControlResultAdapterRegistry().adapt(tmp_path, event)

    assert adapted.adapter_id == "workflow-read-result-v1"
    assert adapted.payload["schema_version"] == "workflow-read-result.v1"
    with pytest.raises(ControlResultAdapterError):
        ControlResultAdapterRegistry().adapt(
            tmp_path,
            ZfEvent(
                type="issue.triage.child.unexpected",
                payload=event.payload,
            ),
        )


def test_writer_profile_wins_over_inherited_goal_closure_fields() -> None:
    assert call_result_profile_identity(
        operation_type="fanout_writer_child",
        stage_id="prd-lanes-impl",
        payload={
            "closure_identity": "closure-current",
            "goal_claim_set_ref": "artifacts/goal-claims.json",
        },
    ) == ("implementation", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="task-verify",
        payload={"goal_claim_set_ref": "artifacts/goal-claims.json"},
    ) == ("task-verify", "1")
    assert call_result_profile_identity(
        operation_type="fanout_reader_child",
        stage_id="goal-closure",
        payload={"goal_claim_set_ref": "artifacts/goal-claims.json"},
    ) == ("thin-judge-goal-closure", "1")


def test_profile_revision_and_event_mapping_fail_closed(tmp_path: Path) -> None:
    registry = ControlResultAdapterRegistry()
    with pytest.raises(ControlResultAdapterError, match="unknown call-result profile"):
        registry.profile("task-verify", "99")
    with pytest.raises(ControlResultAdapterError, match="not allowed"):
        registry.adapt_semantic_result(
            tmp_path,
            profile_id="implementation",
            revision="1",
            event_type="judge.child.completed",
            semantic_result={"task_id": "T1", "target_commit": "abc"},
            identity={"workflow_run_id": "run-1"},
            source_event_id="evt-source",
            actor="dev-1",
            task_id="T1",
            correlation_id="run-1",
        )


def test_ref_backed_event_hydrates_once_and_hash_mismatch_fails(tmp_path: Path) -> None:
    descriptor = write_immutable_json_sidecar(
        tmp_path,
        {
            "schema_version": "implementation-result.v1",
            "task_id": "T1",
            "target_commit": "abc123",
        },
        root="call-results/control/implementation-result.v1",
        kind="call_control_result",
        schema_version="implementation-result.v1",
        created_by="test",
        source_event_id="evt-source",
    )
    event = ZfEvent(
        type="dev.build.done",
        payload={
            "semantic_result_profile": {
                "profile_id": "implementation",
                "revision": "1",
            },
            "control_result_ref": descriptor,
        },
    )
    hydrated = hydrate_profiled_control_result_event(tmp_path, event)
    assert hydrated.payload["implementation_result"]["target_commit"] == "abc123"
    broken = ZfEvent(
        type=event.type,
        payload={
            **event.payload,
            "control_result_ref": {**descriptor, "sha256": "0" * 64},
        },
    )
    with pytest.raises(ControlResultAdapterError, match="hydration failed"):
        hydrate_profiled_control_result_event(tmp_path, broken)


def test_implementation_result_uses_nested_impl_self_check_verdict(
    tmp_path: Path,
) -> None:
    event = ZfEvent(
        type="dev.build.done",
        task_id="T08-R4",
        payload={
            "workflow_run_id": "run-1",
            "task_id": "T08-R4",
            "target_commit": "abc123",
            "impl_self_check": {
                "verdict": "passed",
                "acceptance_results": [],
            },
        },
    )

    adapted = ControlResultAdapterRegistry().adapt(tmp_path, event)

    assert adapted.payload["verdict"] == "passed"
    assert adapted.payload["self_check"]["verdict"] == "passed"
