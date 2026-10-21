from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_runtime import (
    admit_runtime_call_result,
    has_admitted_semantic_submit_provenance,
    mark_call_operation_started,
    prepare_call_operation,
)
from zf.runtime.artifact_read_ledger import (
    build_input_consumption_policy,
    write_input_consumption_policy,
)
from zf.runtime.result_submit import (
    ResultSubmitError,
    SemanticResultSubmitService,
    _compatibility_projection,
    is_authorized_result_scratch_write,
    provision_role_submit_credential,
)
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.runtime.fanout import validate_fanout_report
from zf.runtime.task_attempt_runtime import validate_task_attempt_result
from zf.runtime.workflow_operation import WorkflowOperationService
from zf.cli.main import build_parser
from zf.cli.result import _print_error


def _runtime(tmp_path: Path):
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    log = EventLog(state_dir / "events.jsonl")
    return SimpleNamespace(
        project_root=project_root,
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
        config=SimpleNamespace(
            workflow=SimpleNamespace(flow_metadata={"result_protocol": {
                "mode": "blocking",
                "semantic_submit_profiles": {"implementation": "blocking"},
            }})
        ),
    )


def _running_operation(tmp_path: Path):
    runtime = _runtime(tmp_path)
    token_path = provision_role_submit_credential(runtime.state_dir, "dev-1")
    token = token_path.read_text().strip()
    payload = {
        "workflow_run_id": "run-1",
        "role_instance": "dev-1",
        "fanout_id": "fanout-1",
        "stage_id": "impl",
        "child_id": "dev-1-T1",
        "run_id": "attempt-1",
        "task_id": "T1",
        "canonical_success_event": "dev.build.done",
        "canonical_failure_event": "dev.blocked",
    }
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_writer_child",
        operation_key="dev-1-T1",
        stage_id="impl",
        task_id="T1",
        dispatch_id="attempt-1",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="T1",
        dispatch_id="attempt-1",
    )
    service = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    return runtime, prepared, service, token


def _semantic() -> dict:
    return {
        "verdict": "passed",
        "target_commit": "abc123",
        "changed_files": ["result.txt"],
        "evidence_refs": ["receipt:test"],
        "self_check": {"status": "passed"},
        "known_gaps": [],
        "summary": "implemented",
    }


def test_task_pipeline_submit_uses_current_attempt_delivery_policy(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    service = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    required_reads = [{
        "source_id": "plan-port-planning_result",
        "artifact_id": "planning_result",
        "artifact_sha256": "a" * 64,
        "json_path": "$",
        "min_returned_bytes": 1,
    }]
    old_policy = build_input_consumption_policy(
        workflow_run_id="run-1",
        attempt_id="attempt-1",
        required_reads=required_reads,
    )
    old_descriptor = write_input_consumption_policy(
        runtime.state_dir,
        old_policy,
    )
    current_policy = build_input_consumption_policy(
        workflow_run_id="run-1",
        attempt_id="attempt-2",
        required_reads=required_reads,
    )
    current_descriptor = write_input_consumption_policy(
        runtime.state_dir,
        current_policy,
    )
    runtime.event_writer.append(ZfEvent(
        type="task.pipeline.stage.dispatched",
        actor="orchestrator",
        origin="kernel",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": "operation-1",
            "attempt_id": "attempt-2",
            "dispatch_id": "dispatch-2",
            "placement_epoch": 2,
            "task_stage_session_binding": "binding-1",
            "input_consumption_policy_ref": current_descriptor,
        },
    ))
    operation = {
        "operation_id": "operation-1",
        "active_attempt_id": "attempt-2",
        "dispatch_id": "dispatch-2",
    }
    request = {
        "task_pipeline_stage": "impl",
        "input_consumption_policy_ref": old_descriptor,
    }

    delivery = service._current_task_pipeline_delivery(operation, request)
    resolved = service._input_policy(
        request,
        task_pipeline_delivery=delivery,
    )

    assert delivery["placement_epoch"] == 2
    assert resolved["attempt_id"] == "attempt-2"


def _plan_candidate() -> dict:
    return {
        "status": "passed",
        "recommendation": "approve",
        "summary": "Plan candidate is complete.",
        "findings": [],
        "plan_md": "# Plan\n\nImplement TASK-1.",
        "source_index": {
            "schema_version": "source-index.v1",
            "tasks": [{
                "task_id": "TASK-1",
                "source_key": "issue:one",
                "source_ref": "docs/issues/one.md",
                "source_excerpt": "The command succeeds.",
            }],
        },
        "task_map": {
            "schema_version": "task-map.v1",
            "goal_claims": [{
                "goal_claim_id": "CLAIM-1",
                "text": "The command succeeds.",
                "mandatory": True,
            }],
            "tasks": [{
                "task_id": "TASK-1",
                "title": "Implement the command",
                "goal_claim_ids": ["CLAIM-1"],
                "allowed_paths": ["src/command.py", "tests/test_command.py"],
                "allowed_paths_reason": "Own implementation and test.",
                "blocked_by": [],
                "validation": {"commands": [{
                    "id": "test-command",
                    "command": "pytest -q tests/test_command.py",
                    "acceptance_ids": ["AC-1"],
                    "owner": "task_verify",
                    "tier": "runtime",
                    "deterministic": True,
                    "reusable": True,
                    "timeout_seconds": 60,
                }]},
                "acceptance_criteria": [{
                    "id": "AC-1",
                    "statement": "The command succeeds.",
                    "mandatory": True,
                    "verification_owner": "task_verify",
                    "verification_tier": "runtime",
                    "verification_command_ids": ["test-command"],
                }],
            }],
        },
    }


def _running_plan_candidate_operation(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.config.workflow.flow_metadata["result_protocol"][
        "semantic_submit_profiles"
    ]["workflow-read"] = "blocking"
    token_path = provision_role_submit_credential(runtime.state_dir, "planner")
    payload = {
        "workflow_run_id": "run-plan-candidate",
        "role_instance": "planner",
        "fanout_id": "fanout-plan-candidate",
        "stage_id": "issue-triage",
        "child_id": "planner",
        "run_id": "attempt-plan-candidate",
        "attempt_domain": "plan",
        "canonical_success_event": "issue.triage.child.completed",
        "canonical_failure_event": "issue.triage.child.failed",
        "plan_candidate_validation": {
            "schema_version": "plan-candidate-validation-context.v1",
            "manifest": {
                "fanout_id": "fanout-plan-candidate",
                "stage_id": "issue-triage",
                "trigger_payload": {
                    "flow_kind": "issue",
                    "issue_ref": "docs/issues/one.md",
                },
            },
            "metadata": {"flow_kind": "issue"},
        },
    }
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="planner",
        stage_id="issue-triage",
        task_id="",
        dispatch_id="attempt-plan-candidate",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="",
        dispatch_id="attempt-plan-candidate",
    )
    service = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    return runtime, prepared, service, token_path.read_text().strip()


def _running_plan_synth_operation(tmp_path: Path):
    runtime = _runtime(tmp_path)
    token_path = provision_role_submit_credential(
        runtime.state_dir,
        "plan-critic",
    )
    token = token_path.read_text().strip()
    payload = {
        "workflow_run_id": "run-plan",
        "role_instance": "plan-critic",
        "fanout_id": "fanout-plan",
        "stage_id": "plan",
        "child_id": "synth",
        "run_id": "attempt-plan-synth",
        "canonical_success_event": "fanout.synth.completed",
        "canonical_failure_event": "fanout.synth.completed",
        "output_profile_id": "plan-synth",
        "output_profile_revision": "1",
        "plan_revision": "plan-r1",
        "plan_synth_contract_ref": "artifacts/contracts/plan-r1.json",
        "plan_synth_contract_digest": "a" * 64,
        "reviewed_plan_candidate_digest": "a" * 64,
    }
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_synth",
        operation_key="synth@plan",
        stage_id="plan",
        task_id="",
        dispatch_id="attempt-plan-synth",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="",
        dispatch_id="attempt-plan-synth",
    )
    service = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    return runtime, prepared, service, token


def _running_workflow_read_operation(
    tmp_path: Path,
    *,
    artifact_output: bool = False,
):
    runtime = _runtime(tmp_path)
    runtime.config.workflow.flow_metadata["result_protocol"][
        "semantic_submit_profiles"
    ]["workflow-read"] = "blocking"
    token_path = provision_role_submit_credential(
        runtime.state_dir,
        "scan-verification",
    )
    payload = {
        "workflow_run_id": "run-scan",
        "role_instance": "scan-verification",
        "fanout_id": "fanout-scan",
        "stage_id": "flow-scan",
        "child_id": "scan-verification",
        "run_id": "attempt-scan-verification",
        "task_id": "REFACTOR-1",
        "canonical_success_event": "workflow.child.completed",
        "canonical_failure_event": "workflow.child.failed",
    }
    if artifact_output:
        payload.update({
            "generic_workflow_operation": "audit.scope",
            "result_semantics": "artifact_production",
            "workflow_output_ports": [{
                "name": "scope",
                "kind": "audit/scope",
            }],
        })
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="scan-verification",
        stage_id="flow-scan",
        task_id="REFACTOR-1",
        dispatch_id="attempt-scan-verification",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="REFACTOR-1",
        dispatch_id="attempt-scan-verification",
    )
    service = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    return runtime, prepared, service, token_path.read_text().strip()


def _verification_event(
    state_dir: Path, *, operation_id: str, request_hash: str,
) -> ZfEvent:
    from tests.test_call_result_admission import _persisted_verification_payload

    event = ZfEvent(
        type="verify.child.completed",
        actor="verify-1",
        task_id="T-VERIFY",
        correlation_id="run-verify",
        payload={
            "workflow_run_id": "run-verify",
            "operation_id": operation_id,
            "request_hash": request_hash,
            "attempt_id": "attempt-verify",
            "dispatch_id": "dispatch-verify",
            "lease_id": "lease-verify",
        },
    )
    event.payload.update(_persisted_verification_payload(
        state_dir, "passed", identity={
            "workflow_run_id": "run-verify", "task_id": "T-VERIFY",
        },
    ))
    return event


def _pin_verification_operation(
    runtime,
    *,
    operation_id: str,
    semantic_submit_mode: str,
) -> str:
    operations = WorkflowOperationService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    result = _verification_event(
        runtime.state_dir, operation_id=operation_id, request_hash="",
    ).payload["verification_result"]
    ensured = operations.ensure_operation(
        workflow_run_id="run-verify",
        operation_id=operation_id,
        operation_type="fanout_reader_child",
        request={
            "semantic_result_submit_mode": semantic_submit_mode,
            "output_profile_id": "task-verify",
            "output_profile_revision": "1",
            "result_identity": {key: result[key] for key in (
                "workflow_run_id", "task_id", "contract_revision",
                "task_map_generation", "base_commit", "task_ref",
                "contract_snapshot_ref", "contract_snapshot_digest",
                "target_snapshot_ref", "target_snapshot_digest", "target_commit",
            )},
        },
        task_id="T-VERIFY",
        role_instance="verify-1",
        active_attempt_id="attempt-verify",
        lease_id="lease-verify",
    )
    operations.mark_started(
        operation_id=operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id="run-verify",
        task_id="T-VERIFY",
        dispatch_id="dispatch-verify",
        role_instance="verify-1",
        active_attempt_id="attempt-verify",
        lease_id="lease-verify",
    )
    return ensured.request_hash


def test_verification_projection_preserves_evidence_refs() -> None:
    projection = _compatibility_projection(
        "verification_result",
        {
            "verification_result": {
                "verdict": "passed",
                "summary": "verified",
                "evidence_refs": ["receipt:verify"],
            },
        },
    )

    assert projection["report"]["evidence_refs"] == ["receipt:verify"]


def test_implementation_projection_preserves_plan_level_blocker_metadata() -> None:
    projection = _compatibility_projection(
        "implementation_result",
        {
            "implementation_result": {
                "verdict": "blocked",
                "summary": "upstream controls are outside this task scope",
                "failure_class": "upstream_contract_gap",
                "blocker_kind": "upstream_contract_gap",
                "evidence_refs": ["file:src/upstream.ts"],
                "known_gaps": ["free camera belongs to the upstream slice"],
            },
        },
    )

    assert projection["failure_class"] == "upstream_contract_gap"
    assert projection["blocker_kind"] == "upstream_contract_gap"
    assert projection["reason"] == (
        "upstream controls are outside this task scope"
    )


def test_stdin_semantic_submit_fills_identity_and_emits_canonical_event(tmp_path: Path) -> None:
    runtime, prepared, service, token = _running_operation(tmp_path)
    result = service.submit(
        operation_id=prepared.operation_id,
        semantic_result=_semantic(),
        role_instance="dev-1",
        credential=token,
    )
    events = runtime.event_log.read_all()
    canonical = next(event for event in events if event.id == result.canonical_event_id)
    assert result.canonical_event_type == "dev.build.done"
    assert canonical.payload["workflow_run_id"] == "run-1"
    assert canonical.payload["operation_id"] == prepared.operation_id
    assert canonical.payload["source_commit"] == "abc123"
    assert canonical.payload["changed_files"] == ["result.txt"]
    assert (
        canonical.payload["semantic_submit_admission_event_id"]
        == result.admitted_event_id
    )
    assert has_admitted_semantic_submit_provenance(runtime, canonical)
    assert validate_task_attempt_result(
        runtime,
        canonical,
        task=SimpleNamespace(id="T1"),
    ) == ""
    assert not any(
        event.type == "task.attempt.shadow_mismatch"
        for event in runtime.event_log.read_all()
    )
    assert "implementation_result" not in canonical.payload
    hydrated = hydrate_profiled_control_result_event(runtime.state_dir, canonical)
    assert hydrated.payload["implementation_result"]["task_id"] == "T1"
    assert sum(event.type == "workflow.call.result.admitted" for event in events) == 1
    with pytest.raises(ResultSubmitError) as duplicate:
        service.submit(
            operation_id=prepared.operation_id,
            semantic_result=_semantic(),
            role_instance="dev-1",
            credential=token,
        )
    assert duplicate.value.code == "duplicate_submit"


def test_plan_candidate_validate_can_fail_fix_and_submit_same_operation(
    tmp_path: Path,
) -> None:
    runtime, prepared, service, token = _running_plan_candidate_operation(
        tmp_path
    )
    invalid = _plan_candidate()
    invalid["task_map"]["tasks"][0]["acceptance_criteria"][0].pop(
        "verification_command_ids"
    )
    before_ids = [event.id for event in runtime.event_log.read_all()]

    failed = service.validate_plan_candidate(
        operation_id=prepared.operation_id,
        semantic_result=invalid,
        role_instance="planner",
        credential=token,
        project_root=runtime.project_root,
    )
    passed = service.validate_plan_candidate(
        operation_id=prepared.operation_id,
        semantic_result=_plan_candidate(),
        role_instance="planner",
        credential=token,
        project_root=runtime.project_root,
    )

    assert failed["status"] == "failed"
    assert "acceptance_command_missing" in {
        item["code"] for item in failed["errors"]
    }
    assert passed["status"] == "passed"
    assert [event.id for event in runtime.event_log.read_all()] == before_ids
    submitted = service.submit(
        operation_id=prepared.operation_id,
        semantic_result=_plan_candidate(),
        role_instance="planner",
        credential=token,
    )
    assert submitted.canonical_event_type == "issue.triage.child.completed"


def test_plan_candidate_submit_cannot_bypass_same_attempt_preflight(
    tmp_path: Path,
) -> None:
    runtime, prepared, service, token = _running_plan_candidate_operation(
        tmp_path
    )
    invalid = _plan_candidate()
    invalid["task_map"]["tasks"][0]["acceptance_criteria"][0].pop(
        "verification_command_ids"
    )

    with pytest.raises(ResultSubmitError) as rejected:
        service.submit(
            operation_id=prepared.operation_id,
            semantic_result=invalid,
            role_instance="planner",
            credential=token,
        )

    assert rejected.value.code == "plan_candidate_not_admitted"
    assert rejected.value.details["attempt_domain"] == "protocol_repair"
    assert rejected.value.details["semantic_rework_cost"] == 0
    assert not any(
        event.type == "workflow.call.result.admitted"
        for event in runtime.event_log.read_all()
    )
    submitted = service.submit(
        operation_id=prepared.operation_id,
        semantic_result=_plan_candidate(),
        role_instance="planner",
        credential=token,
    )
    assert submitted.canonical_event_type == "issue.triage.child.completed"


def test_plan_synth_validate_uses_typed_profile_without_candidate_context(
    tmp_path: Path,
) -> None:
    runtime, prepared, service, token = _running_plan_synth_operation(tmp_path)
    semantic = {
        "status": "completed",
        "recommendation": "approve",
        "workflow_run_id": "run-plan",
        "fanout_id": "fanout-plan",
        "stage_id": "plan",
        "plan_revision": "plan-r1",
        "plan_synth_contract_ref": "artifacts/contracts/plan-r1.json",
        "plan_synth_contract_digest": "a" * 64,
        "reviewed_plan_candidate_digest": "a" * 64,
        "summary": "plan approved",
        "report": {
            "child_id": "synth",
            "status": "passed",
            "recommendation": "approve",
            "summary": "plan approved",
            "findings": [{
                "severity": "residual-risk",
                "message": "Implementation evidence is still required.",
            }],
        },
    }
    before_ids = [event.id for event in runtime.event_log.read_all()]

    failed = service.validate_plan_candidate(
        operation_id=prepared.operation_id,
        semantic_result=semantic,
        role_instance="plan-critic",
        credential=token,
        project_root=runtime.project_root,
    )
    semantic["report"]["findings"][0]["severity"] = "low"
    passed = service.validate_plan_candidate(
        operation_id=prepared.operation_id,
        semantic_result=semantic,
        role_instance="plan-critic",
        credential=token,
        project_root=runtime.project_root,
    )

    assert failed["status"] == "failed"
    assert any(
        "severity must be one of" in item["message"]
        for item in failed["errors"]
    )
    assert passed == {
        "schema_version": "semantic-result-preflight.v1",
        "status": "passed",
        "profile_id": "plan-synth",
        "profile_revision": "1",
        "errors": [],
    }
    assert [event.id for event in runtime.event_log.read_all()] == before_ids
    submitted = service.submit(
        operation_id=prepared.operation_id,
        semantic_result=semantic,
        role_instance="plan-critic",
        credential=token,
    )
    assert submitted.canonical_event_type == "fanout.synth.completed"


def test_result_validate_cli_is_registered() -> None:
    args = build_parser().parse_args([
        "result",
        "validate",
        "--operation",
        "operation-1",
        "--result-file",
        "/tmp/result.json",
    ])

    assert args.result_command == "validate"
    assert args.operation == "operation-1"


def test_result_submit_cli_accepts_operation_scratch() -> None:
    args = build_parser().parse_args([
        "result",
        "submit",
        "--operation",
        "operation-1",
        "--scratch",
    ])

    assert args.result_command == "submit"
    assert args.scratch is True


def test_workflow_read_submit_emits_registered_product_child_event(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    token_path = provision_role_submit_credential(
        runtime.state_dir,
        "issue-triage",
    )
    payload = {
        "workflow_run_id": "run-issue",
        "role_instance": "issue-triage",
        "fanout_id": "fanout-issue",
        "stage_id": "issue-triage",
        "child_id": "issue-triage",
        "run_id": "attempt-issue-triage",
        "task_id": "ISSUE-ANCHOR",
        "canonical_success_event": "issue.triage.child.completed",
        "canonical_failure_event": "issue.triage.child.failed",
    }
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="issue-triage",
        stage_id="issue-triage",
        task_id="ISSUE-ANCHOR",
        dispatch_id="attempt-issue-triage",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="ISSUE-ANCHOR",
        dispatch_id="attempt-issue-triage",
    )

    result = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    ).submit(
        operation_id=prepared.operation_id,
        semantic_result={
            "status": "passed",
            "summary": "triage complete",
            "recommendation": "approve",
            "findings": [],
        },
        role_instance="issue-triage",
        credential=token_path.read_text().strip(),
    )

    canonical = next(
        event
        for event in runtime.event_log.read_all()
        if event.id == result.canonical_event_id
    )
    assert prepared.output_profile_id == "workflow-read"
    assert result.canonical_event_type == "issue.triage.child.completed"
    assert canonical.payload["semantic_result_profile"]["profile_id"] == (
        "workflow-read"
    )
    hydrated = hydrate_profiled_control_result_event(
        runtime.state_dir,
        canonical,
    )
    assert hydrated.payload["report"]["schema_version"] == (
        "workflow-read-result.v1"
    )
    assert hydrated.payload["report"]["summary"] == "triage complete"


def test_workflow_read_submit_rejects_ambiguous_wrapper_without_losing_retry(
    tmp_path: Path,
) -> None:
    runtime, prepared, service, token = _running_workflow_read_operation(tmp_path)
    scratch = runtime.state_dir / prepared.result_scratch_ref
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text(json.dumps({
        "status": "passed",
        "summary": "complete evidence-backed scan",
        "recommendation": "approve",
        "coverage_matrix": [{"subsystem": "runtime", "coverage": "covered"}],
        "evidence_refs": ["file:src/runtime.py:1"],
        "report": {
            "status": "passed",
            "summary": "nested legacy report",
            "recommendation": "approve",
            "findings": [],
        },
    }))

    with pytest.raises(ResultSubmitError) as ambiguous:
        service.submit(
            operation_id=prepared.operation_id,
            result_file=scratch,
            role_instance="scan-verification",
            credential=token,
        )
    assert ambiguous.value.code == "ambiguous_semantic_result"

    scratch.write_text(json.dumps({
        "status": "passed",
        "summary": "complete evidence-backed scan",
        "recommendation": "approve",
        "coverage_matrix": [{"subsystem": "runtime", "coverage": "covered"}],
        "evidence_refs": ["file:src/runtime.py:1"],
        "findings": [],
    }))
    submitted = service.submit(
        operation_id=prepared.operation_id,
        result_file=scratch,
        role_instance="scan-verification",
        credential=token,
    )
    canonical = next(
        event for event in runtime.event_log.read_all()
        if event.id == submitted.canonical_event_id
    )
    hydrated = hydrate_profiled_control_result_event(runtime.state_dir, canonical)
    assert hydrated.payload["report"]["coverage_matrix"] == [
        {"subsystem": "runtime", "coverage": "covered"},
    ]
    assert hydrated.payload["report"]["evidence_refs"] == [
        "file:src/runtime.py:1",
    ]


def test_workflow_output_rejection_returns_actionable_cli_diagnostics(
    tmp_path: Path,
    capsys,
) -> None:
    runtime, prepared, service, token = _running_workflow_read_operation(
        tmp_path,
        artifact_output=True,
    )
    incomplete = {
        "execution_status": "completed",
        "status": "passed",
        "summary": "Scope evidence is complete.",
        "recommendation": "approve",
        "findings": [],
    }

    with pytest.raises(ResultSubmitError) as rejected:
        service.submit(
            operation_id=prepared.operation_id,
            semantic_result=incomplete,
            role_instance="scan-verification",
            credential=token,
        )

    assert rejected.value.code == "result_not_admitted"
    assert rejected.value.details["status"] == "repair_pending"
    assert rejected.value.details["issues"] == [{
        "field": "control_result.workflow_output_ports[0]",
        "code": "workflow_output_missing",
        "message": "flow-scan.scope",
        "required_change": (
            "Provide the non-placeholder artifact body at outputs.scope "
            "for declared port flow-scan.scope."
        ),
    }]
    _print_error(rejected.value, fallback_code="result_submit_failed")
    stderr = capsys.readouterr().err
    assert '"code": "workflow_output_missing"' in stderr
    assert "outputs.scope" in stderr

    admitted = service.submit(
        operation_id=prepared.operation_id,
        semantic_result={
            **incomplete,
            "outputs": {
                "scope": {
                    "acceptance_ids": ["AUD-AC-001"],
                    "conclusion": "not_ready",
                },
            },
        },
        role_instance="scan-verification",
        credential=token,
    )
    assert admitted.canonical_event_type == "workflow.child.completed"


def test_plan_synth_legacy_reject_projects_normalized_verdict(
    tmp_path: Path,
) -> None:
    runtime, prepared, service, token = _running_plan_synth_operation(tmp_path)
    result = service.submit(
        operation_id=prepared.operation_id,
        semantic_result={
            "status": "completed",
            "recommendation": "reject",
            "workflow_run_id": "run-plan",
            "fanout_id": "fanout-plan",
            "stage_id": "plan",
            "plan_revision": "plan-r1",
            "plan_synth_contract_ref": "artifacts/contracts/plan-r1.json",
            "plan_synth_contract_digest": "a" * 64,
            "summary": "plan requires rework",
            "report": {
                "status": "failed",
                "recommendation": "reject",
                "summary": "plan requires rework",
                "findings": [{
                    "severity": "blocker",
                    "code": "plan_ports_not_descriptor_array",
                    "field": "plan_ports",
                    "observed": "plan_ports contains strings",
                    "expected": "plan_ports contains descriptors",
                }],
                "fix_items": [{
                    "task_id": "refactor-plan-synth",
                    "acceptance_id": "plan_ports",
                    "observed_gap": "The child returned strings.",
                    "required_change": "Return descriptor objects.",
                    "done_when": "Every required port has a body.",
                }],
                "evidence_refs": [],
            },
        },
        role_instance="plan-critic",
        credential=token,
    )

    canonical = next(
        event for event in runtime.event_log.read_all()
        if event.id == result.canonical_event_id
    )
    assert canonical.payload["report"]["status"] == "failed"
    assert canonical.payload["report"]["recommendation"] == "reject"
    hydrated = hydrate_profiled_control_result_event(runtime.state_dir, canonical)
    assert hydrated.payload["plan_synthesis_result"]["verdict"] == "rejected"
    assert hydrated.payload["plan_synthesis_result"]["fix_items"] == [{
        "task_id": "refactor-plan-synth",
        "acceptance_id": "plan_ports",
        "observed_gap": "The child returned strings.",
        "required_change": "Return descriptor objects.",
        "done_when": "Every required port has a body.",
    }]


def test_plan_synth_legacy_string_feedback_is_preserved(
    tmp_path: Path,
) -> None:
    runtime, prepared, service, token = _running_plan_synth_operation(tmp_path)
    result = service.submit(
        operation_id=prepared.operation_id,
        semantic_result={
            "status": "completed",
            "recommendation": "reject",
            "workflow_run_id": "run-plan",
            "fanout_id": "fanout-plan",
            "stage_id": "plan",
            "plan_revision": "plan-r1",
            "plan_synth_contract_ref": "artifacts/contracts/plan-r1.json",
            "plan_synth_contract_digest": "a" * 64,
            "summary": "plan requires rework",
            "report": {
                "status": "failed",
                "recommendation": "reject",
                "summary": "plan requires rework",
                "findings": ["Foundation scope exceeds the writer limit."],
                "fix_items": ["Split Foundation into bounded work units."],
                "evidence_refs": [],
            },
        },
        role_instance="plan-critic",
        credential=token,
    )

    canonical = next(
        event for event in runtime.event_log.read_all()
        if event.id == result.canonical_event_id
    )
    hydrated = hydrate_profiled_control_result_event(runtime.state_dir, canonical)
    plan_result = hydrated.payload["plan_synthesis_result"]
    assert plan_result["findings"][0]["message"] == (
        "Foundation scope exceeds the writer limit."
    )
    assert plan_result["fix_items"][0]["required_change"] == (
        "Split Foundation into bounded work units."
    )
    assert canonical.payload["report"]["fix_items"][0]["required_change"] == (
        "Split Foundation into bounded work units."
    )


def test_plan_synth_zero_line_projects_as_missing_optional_line(
    tmp_path: Path,
) -> None:
    runtime, prepared, service, token = _running_plan_synth_operation(tmp_path)
    result = service.submit(
        operation_id=prepared.operation_id,
        semantic_result={
            "status": "completed",
            "recommendation": "approve",
            "workflow_run_id": "run-plan",
            "fanout_id": "fanout-plan",
            "stage_id": "plan",
            "plan_revision": "plan-r1",
            "plan_synth_contract_ref": "artifacts/contracts/plan-r1.json",
            "plan_synth_contract_digest": "a" * 64,
            "summary": "plan approved",
            "report": {
                "status": "passed",
                "recommendation": "approve",
                "summary": "plan approved",
                "findings": [{
                    "severity": "info",
                    "category": "residual_risk",
                    "path": "docs/plans/plan.md",
                    "line": 0,
                    "message": "Implementation evidence is still required.",
                }],
                "evidence_refs": ["docs/plans/plan.md"],
            },
        },
        role_instance="plan-critic",
        credential=token,
    )

    canonical = next(
        event for event in runtime.event_log.read_all()
        if event.id == result.canonical_event_id
    )
    assert "line" not in canonical.payload["report"]["findings"][0]
    validated = validate_fanout_report(
        canonical.payload["report"],
        child_id="synth",
    )
    assert validated.valid is True
    hydrated = hydrate_profiled_control_result_event(runtime.state_dir, canonical)
    assert "line" not in hydrated.payload["plan_synthesis_result"]["findings"][0]


def test_plan_synth_submit_preserves_name_to_body_plan_ports(
    tmp_path: Path,
) -> None:
    runtime, prepared, service, token = _running_plan_synth_operation(tmp_path)
    body = {
        "schema_version": "acceptance-matrix.v1",
        "status": "ready",
        "metadata": {
            "enrichment_contract": {"status": "fulfilled"},
        },
        "rows": [{"acceptance_id": "AC-1"}],
    }
    result = service.submit(
        operation_id=prepared.operation_id,
        semantic_result={
            "status": "completed",
            "recommendation": "approve",
            "summary": "plan approved",
            "plan_ports": {"acceptance_matrix": body},
            "report": {
                "status": "passed",
                "recommendation": "approve",
                "summary": "plan approved",
                "findings": [],
                "evidence_refs": [],
            },
        },
        role_instance="plan-critic",
        credential=token,
    )

    canonical = next(
        event for event in runtime.event_log.read_all()
        if event.id == result.canonical_event_id
    )
    hydrated = hydrate_profiled_control_result_event(runtime.state_dir, canonical)
    assert hydrated.payload["plan_synthesis_result"]["plan_ports"] == [{
        "logical_name": "acceptance_matrix",
        "schema_version": "acceptance-matrix.v1",
        "body": body,
    }]


def test_blocking_operation_rejects_legacy_terminal_until_secure_submit(
    tmp_path: Path,
) -> None:
    runtime, prepared, service, token = _running_operation(tmp_path)
    legacy = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="T1",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": prepared.operation_id,
            "request_hash": prepared.request_hash,
            "attempt_id": "attempt-1",
            "dispatch_id": "attempt-1",
            "lease_id": "attempt-1",
            "implementation_result": _semantic(),
        },
    )

    blocked = admit_runtime_call_result(
        runtime,
        legacy,
        dispatch_correction=False,
    )

    assert blocked.repair_requested
    assert any(
        issue.get("code") == "semantic_submit_required"
        for issue in blocked.issues
    )
    submitted = service.submit(
        operation_id=prepared.operation_id,
        semantic_result=_semantic(),
        role_instance="dev-1",
        credential=token,
    )
    canonical = next(
        event for event in runtime.event_log.read_all()
        if event.id == submitted.canonical_event_id
    )
    forged = ZfEvent(
        type=canonical.type,
        actor=canonical.actor,
        task_id=canonical.task_id,
        correlation_id=canonical.correlation_id,
        payload={
            **canonical.payload,
            "semantic_submit_admission_event_id": "evt-forged",
        },
    )
    forged_outcome = admit_runtime_call_result(
        runtime,
        forged,
        dispatch_correction=False,
    )
    assert forged_outcome.repair_requested
    assert any(
        issue.get("code") == "semantic_submit_required"
        for issue in forged_outcome.issues
    )
    admitted = admit_runtime_call_result(
        runtime,
        canonical,
        dispatch_correction=False,
    )
    assert admitted.admitted
    assert sum(
        event.type == "workflow.call.result.admitted"
        for event in runtime.event_log.read_all()
    ) == 1


def test_verify_legacy_terminal_obeys_pinned_semantic_submit_mode(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    blocking_hash = _pin_verification_operation(
        runtime,
        operation_id="op-verify-blocking",
        semantic_submit_mode="blocking",
    )

    blocked = admit_runtime_call_result(
        runtime,
        _verification_event(
            runtime.state_dir,
            operation_id="op-verify-blocking",
            request_hash=blocking_hash,
        ),
        dispatch_correction=False,
    )

    assert blocked.repair_requested
    assert any(
        issue.get("code") == "semantic_submit_required"
        for issue in blocked.issues
    )

    off_hash = _pin_verification_operation(
        runtime,
        operation_id="op-verify-off",
        semantic_submit_mode="off",
    )
    compatible = admit_runtime_call_result(
        runtime,
        _verification_event(
            runtime.state_dir,
            operation_id="op-verify-off",
            request_hash=off_hash,
        ),
        dispatch_correction=False,
    )
    assert compatible.admitted
    assert not any(
        issue.get("code") == "semantic_submit_required"
        for issue in compatible.issues
    )


def test_submit_rejects_other_role_and_stale_credential(tmp_path: Path) -> None:
    runtime, prepared, service, token = _running_operation(tmp_path)
    with pytest.raises(ResultSubmitError) as wrong_role:
        service.submit(
            operation_id=prepared.operation_id,
            semantic_result=_semantic(),
            role_instance="verify-1",
            credential=token,
        )
    assert wrong_role.value.code == "role_mismatch"

    new_token_path = provision_role_submit_credential(runtime.state_dir, "dev-1", rotate=True)
    with pytest.raises(ResultSubmitError) as stale:
        service.submit(
            operation_id=prepared.operation_id,
            semantic_result=_semantic(),
            role_instance="dev-1",
            credential=token,
        )
    assert stale.value.code == "capability_invalid"
    result = service.submit(
        operation_id=prepared.operation_id,
        semantic_result=_semantic(),
        role_instance="dev-1",
        credential=new_token_path.read_text().strip(),
    )
    assert result.canonical_event_type == "dev.build.done"


def test_result_file_requires_exact_regular_scratch_path(tmp_path: Path) -> None:
    runtime, prepared, service, token = _running_operation(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_semantic()))
    with pytest.raises(ResultSubmitError) as escaped:
        service.submit(
            operation_id=prepared.operation_id,
            result_file=outside,
            role_instance="dev-1",
            credential=token,
        )
    assert escaped.value.code == "result_file_outside_scratch"

    scratch = runtime.state_dir / prepared.result_scratch_ref
    scratch.parent.mkdir(parents=True)
    scratch.symlink_to(outside)
    with pytest.raises(ResultSubmitError) as symlink:
        service.submit(
            operation_id=prepared.operation_id,
            result_file=scratch,
            role_instance="dev-1",
            credential=token,
        )
    assert symlink.value.code == "result_file_unsafe"


def test_operation_scratch_resolves_the_signed_result_path(tmp_path: Path) -> None:
    runtime, prepared, service, token = _running_operation(tmp_path)
    scratch = runtime.state_dir / prepared.result_scratch_ref
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text(json.dumps(_semantic()), encoding="utf-8")

    with pytest.raises(ResultSubmitError) as ambiguous:
        service.submit(
            operation_id=prepared.operation_id,
            semantic_result=_semantic(),
            use_operation_scratch=True,
            role_instance="dev-1",
            credential=token,
        )
    assert ambiguous.value.code == "input_mode_invalid"

    result = service.submit(
        operation_id=prepared.operation_id,
        use_operation_scratch=True,
        role_instance="dev-1",
        credential=token,
    )

    assert result.canonical_event_type == "dev.build.done"


def test_result_scratch_write_authorization_is_exact_and_current(
    tmp_path: Path,
) -> None:
    runtime, prepared, service, token = _running_operation(tmp_path)
    del service, token
    scratch = runtime.state_dir / prepared.result_scratch_ref
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text("{}\n", encoding="utf-8")
    sibling = scratch.with_name("other.json")
    sibling.write_text("{}\n", encoding="utf-8")

    assert is_authorized_result_scratch_write(
        runtime.state_dir,
        runtime.event_log,
        role_instance="dev-1",
        target=scratch,
    )
    assert not is_authorized_result_scratch_write(
        runtime.state_dir,
        runtime.event_log,
        role_instance="verify-1",
        target=scratch,
    )
    assert not is_authorized_result_scratch_write(
        runtime.state_dir,
        runtime.event_log,
        role_instance="dev-1",
        target=sibling,
    )

    scratch.unlink()
    scratch.symlink_to(sibling)
    assert not is_authorized_result_scratch_write(
        runtime.state_dir,
        runtime.event_log,
        role_instance="dev-1",
        target=scratch,
    )


def test_new_attempt_supersedes_previous_result_scratch_write(
    tmp_path: Path,
) -> None:
    runtime, previous, service, token = _running_operation(tmp_path)
    del service, token
    previous_scratch = runtime.state_dir / previous.result_scratch_ref
    previous_scratch.parent.mkdir(parents=True, exist_ok=True)
    previous_scratch.write_text("{}\n", encoding="utf-8")
    current = prepare_call_operation(
        runtime,
        payload={
            "workflow_run_id": "run-1",
            "role_instance": "dev-1",
            "fanout_id": "fanout-1",
            "stage_id": "impl",
            "child_id": "dev-1-T1-retry",
            "run_id": "attempt-2",
            "task_id": "T1",
            "canonical_success_event": "dev.build.done",
            "canonical_failure_event": "dev.blocked",
        },
        operation_type="fanout_writer_child",
        operation_key="dev-1-T1-retry",
        stage_id="impl",
        task_id="T1",
        dispatch_id="attempt-2",
    )
    mark_call_operation_started(
        runtime,
        current,
        task_id="T1",
        dispatch_id="attempt-2",
    )
    current_scratch = runtime.state_dir / current.result_scratch_ref
    current_scratch.parent.mkdir(parents=True, exist_ok=True)
    current_scratch.write_text("{}\n", encoding="utf-8")

    assert not is_authorized_result_scratch_write(
        runtime.state_dir,
        runtime.event_log,
        role_instance="dev-1",
        target=previous_scratch,
    )
    assert is_authorized_result_scratch_write(
        runtime.state_dir,
        runtime.event_log,
        role_instance="dev-1",
        target=current_scratch,
    )


def test_result_submit_cli_requires_one_input_mode() -> None:
    parser = build_parser()
    args = parser.parse_args([
        "result", "submit", "--operation", "op-1", "--stdin",
    ])
    assert args.operation == "op-1"
    assert args.stdin is True
    assert callable(args.func)


def test_signed_regular_result_scratch_is_ingested(tmp_path: Path) -> None:
    runtime, prepared, service, token = _running_operation(tmp_path)
    scratch = runtime.state_dir / prepared.result_scratch_ref
    scratch.parent.mkdir(parents=True)
    scratch.write_text(json.dumps(_semantic()))
    result = service.submit(
        operation_id=prepared.operation_id,
        result_file=scratch,
        role_instance="dev-1",
        credential=token,
    )
    assert result.canonical_event_type == "dev.build.done"
