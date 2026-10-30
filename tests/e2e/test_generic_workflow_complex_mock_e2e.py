"""Generic Workflow v1 deterministic closure and replay scenario."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.e2e.generic_workflow_complex_support import (
    REQUEST_ID,
    _RecordingTransport,
    _admit_delivery,
    _append_stage_result,
    _artifact,
    _claim_set,
    _consume_prepared_reads,
    _delivery_result,
    _identity,
    _latest_event,
    _prepare_reads,
    _proposal,
    _settle_mock_generation_fanouts,
    _source,
    _submit_proposal,
    _write_base_config,
)
from zf.cli.flow import build_flow_intake
from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_delivery_result import (
    normalize_artifact_delivery_result,
)
from zf.runtime.artifact_query.service import ArtifactQueryService
from zf.runtime.context_delivery import (
    build_context_delivery_envelope,
    build_execution_binding,
    write_context_delivery_receipt,
)
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.goal_completion_receipt import build_goal_completion_receipt
from zf.runtime.goal_dossier import build_goal_dossier
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_contract import load_run_contract
from zf.runtime.run_manager import (
    run_goal_completion_claim_event,
    run_goal_completion_gate_event,
)
from zf.runtime.simulation_lifecycle import emit_simulation_done
from zf.runtime.workflow_dependency_barrier import (
    reconcile_dependency_barriers,
)
from zf.runtime.workflow_operation import WorkflowOperationService
from zf.runtime.workflow_requests import load_workflow_request


ProviderVerifier = Callable[
    [Path, Path, Mapping[str, Any], Mapping[str, Any]],
    Mapping[str, Any],
]


def run_generic_workflow_complex_scenario(
    tmp_path: Path,
    *,
    provider_verifier: ProviderVerifier | None = None,
    provider_backend: str = "mock",
    report_transform: Callable[[int, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    objective: str = (
        "Research the delivery question and provide a verified report."
    ),
) -> dict[str, Any]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_path = project_root / "zf.yaml"
    _write_base_config(config_path)
    state_dir = project_root / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    intake = build_flow_intake(
        kind="workflow",
        objective=objective,
        backend="mock",
        project_id="generic-complex-mock",
        request_id=REQUEST_ID,
        acceptance=(
            "The report answers the confirmed question with independent evidence.",
        ),
        output=project_root / "docs" / "intake" / f"{REQUEST_ID}.md",
    )
    intake_ref = Path(intake["intake_ref"])
    manifest_ref = Path(intake["workflow_input_manifest_ref"])
    request_v1, _synthesis_v1, preview_v1 = _proposal(
        project_root=project_root,
        state_dir=state_dir,
        config_path=config_path,
        intake_ref=intake_ref,
        manifest_ref=manifest_ref,
        writer=writer,
        confirm=True,
    )
    assert not any(
        document.get("kind") == "Workflow"
        for document in yaml.safe_load_all(config_path.read_text(encoding="utf-8"))
    )
    proposal_v1 = preview_v1["proposal"]
    assert proposal_v1["change_mode"] == "config_change"
    assert proposal_v1["request_revision"] == request_v1["revision"]
    assert len(proposal_v1["stage_graph"]["nodes"]) == 5

    service = ControlledActionService(
        state_dir,
        writer,
        config=load_config(config_path),
        project_root=project_root,
        actor="mock-operator",
        source="generic-workflow-e2e",
        surface="test",
    )
    _submit_proposal(
        service=service,
        writer=writer,
        preview=preview_v1,
        intake_ref=intake_ref,
    )
    invoke_v1 = _latest_event(writer, "workflow.invoke.requested")
    admission_v1 = Orchestrator(
        state_dir,
        load_config(config_path),
        _RecordingTransport(),
        project_root=project_root,
    )
    admission_v1._try_start_declared_workflow_fanout = lambda *a, **k: True
    assert admission_v1._on_workflow_invoke_requested(invoke_v1) is not None
    generation_v1 = str(invoke_v1.payload["workflow_generation"])
    run_contract_v1 = load_run_contract(state_dir)
    assert run_contract_v1 is not None
    assert run_contract_v1["workflow"]["proposal_digest"] == (
        proposal_v1["proposal_digest"]
    )
    assert invoke_v1.payload["effective_config_digest"] == (
        proposal_v1["effective_config_ref"]["sha256"]
    )
    assert invoke_v1.payload["run_contract_digest"] == (
        run_contract_v1["contract_digest"]
    )
    config_v1 = load_config(config_path)
    assert len(config_v1.workflow.generic_workflows) == 1

    requirement_v1 = {
        "ref": str(Path(request_v1["requirement_spec_ref"])),
        "sha256": request_v1["requirement_spec_digest"],
    }
    scope_v1_reads = _prepare_reads(
        state_dir,
        run_id=REQUEST_ID,
        attempt_id="attempt-v1-scope",
        role="scoper",
        sources=[_source(
            requirement_v1,
            source_id="requirement",
            artifact_id="requirement.json",
            kind="requirement/spec",
        )],
    )
    scope_v1 = _artifact(
        state_dir,
        root="workflow/artifacts/scope",
        kind="research/scope",
        schema_version="research-scope.v1",
        created_by="scoper",
        body={
            "schema_version": "research-scope.v1",
            "question": "What evidence supports the confirmed delivery claim?",
        },
    )
    _append_stage_result(
        state_dir,
        writer,
        invoke=invoke_v1,
        stage_id="scope",
        role="scoper",
        output=scope_v1,
        ledger=scope_v1_reads["ledger"],
    )
    evidence_v1: list[dict[str, Any]] = []
    for index, role in enumerate(("collector-a", "collector-b"), start=1):
        reads = _prepare_reads(
            state_dir,
            run_id=REQUEST_ID,
            attempt_id=f"attempt-v1-collect-{index}",
            role=role,
            sources=[_source(
                scope_v1,
                source_id="scope",
                artifact_id="scope.json",
                kind="research/scope",
            )],
        )
        evidence = _artifact(
            state_dir,
            root="workflow/artifacts/evidence",
            kind="research/evidence",
            schema_version="research-evidence.v1",
            created_by=role,
            body={
                "schema_version": "research-evidence.v1",
                "collector": role,
                "finding": f"evidence-{index}",
            },
        )
        evidence_v1.append(evidence)
        _append_stage_result(
            state_dir,
            writer,
            invoke=invoke_v1,
            stage_id=f"collect-{index}",
            role=role,
            output=evidence,
            ledger=reads["ledger"],
        )
    barrier_v1 = reconcile_dependency_barriers(
        config_v1,
        writer.event_log.read_all(),
    )
    assert len(barrier_v1) == 1
    writer.append(barrier_v1[0].to_event())
    synth_v1_reads = _prepare_reads(
        state_dir,
        run_id=REQUEST_ID,
        attempt_id="attempt-v1-synthesize",
        role="synthesizer",
        sources=[
            _source(
                evidence,
                source_id=f"collect-{index}",
                artifact_id=f"evidence-{index}.json",
                kind="research/evidence",
            )
            for index, evidence in enumerate(evidence_v1, start=1)
        ],
    )
    report_v1 = _artifact(
        state_dir,
        root="workflow/artifacts/reports",
        kind="report/markdown",
        schema_version="research-report.v1",
        created_by="synthesizer",
        body={
            "schema_version": "research-report.v1",
            "title": "Initial report",
            "findings": ["Only one evidence family was independently confirmed."],
            **(dict(report_transform(1, {})) if report_transform else {}),
        },
    )
    synth_result_v1 = _append_stage_result(
        state_dir,
        writer,
        invoke=invoke_v1,
        stage_id="synthesize",
        role="synthesizer",
        output=report_v1,
        ledger=synth_v1_reads["ledger"],
    )
    gap_ref = _artifact(
        state_dir,
        root="workflow/artifacts/gaps",
        kind="goal_gap",
        schema_version="goal-gap.v1",
        created_by="verifier",
        body={
            "schema_version": "goal-gap.v1",
            "claim": "independent evidence",
            "reason": "source diversity is insufficient",
            "mandatory": True,
        },
    )
    claim_set_v1, claim_event_v1 = _claim_set(state_dir, writer)
    delivery_v1 = _delivery_result(
        invoke=invoke_v1,
        run_contract=run_contract_v1,
        claim_event=claim_event_v1,
        claim_set=claim_set_v1,
        report=report_v1,
        synthesis_envelope_ref=synth_result_v1["envelope_ref"],
        verdict="rejected",
        gap_ref=gap_ref,
    )
    verify_v1_reads = _prepare_reads(
        state_dir,
        run_id=REQUEST_ID,
        attempt_id="attempt-v1-verify",
        role="verifier",
        sources=[_source(
            report_v1,
            source_id="synthesize",
            artifact_id="report.json",
            kind="report/markdown",
        )],
        consume=False,
    )
    operation_service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
    )
    repair = _admit_delivery(
        state_dir=state_dir,
        writer=writer,
        operation_service=operation_service,
        invoke=invoke_v1,
        result=delivery_v1,
        prepared_reads=verify_v1_reads,
        event_id="evt-v1-verify-malformed",
    )
    assert repair.status == "repair_pending"
    assert repair.repair_round == 1
    verify_v1_ledger = _consume_prepared_reads(
        state_dir,
        verify_v1_reads,
        role="verifier",
    )
    admitted_rejection = _admit_delivery(
        state_dir=state_dir,
        writer=writer,
        operation_service=operation_service,
        invoke=invoke_v1,
        result=delivery_v1,
        prepared_reads=verify_v1_reads,
        event_id="evt-v1-verify-repaired",
    )
    assert admitted_rejection.admitted is True
    rejected_event = writer.append(ZfEvent(
        id="evt-v1-verify-rejected",
        type="verify.completed",
        actor="verifier",
        correlation_id=REQUEST_ID,
        payload={
            **_identity(invoke_v1),
            "stage_id": "verify",
            "role_instance": "verifier",
            "verdict": "rejected",
            "open_gap_refs": [gap_ref["ref"]],
            "admitted_call_result_ref": admitted_rejection.envelope_ref,
            "read_ledger_ref": verify_v1_ledger,
        },
    ))
    assert not any(
        event.type == "run.goal.completed"
        for event in writer.event_log.read_all()
    )

    request_v2, _synthesis_v2, preview_v2 = _proposal(
        project_root=project_root,
        state_dir=state_dir,
        config_path=config_path,
        intake_ref=intake_ref,
        manifest_ref=manifest_ref,
        writer=writer,
        acceptance=[
            "The report answers the confirmed question with two independent "
            "source families.",
        ],
        revision_reason="semantic_replan",
        source_event_id=rejected_event.id,
    )
    proposal_v2 = preview_v2["proposal"]
    assert request_v2["revision"] == request_v1["revision"] + 1
    assert proposal_v2["proposal_digest"] != proposal_v1["proposal_digest"]
    assert proposal_v2["change_mode"] == "run_parameters_only"
    _submit_proposal(
        service=service,
        writer=writer,
        preview=preview_v2,
        intake_ref=intake_ref,
    )
    invoke_v2 = _latest_event(writer, "workflow.invoke.requested")
    admission_v2 = Orchestrator(
        state_dir,
        load_config(config_path),
        _RecordingTransport(),
        project_root=project_root,
    )
    admission_v2._try_start_declared_workflow_fanout = lambda *a, **k: True
    assert admission_v2._on_workflow_invoke_requested(invoke_v2) is not None
    generation_v2 = str(invoke_v2.payload["workflow_generation"])
    assert generation_v2 == proposal_v2["proposal_digest"]
    assert generation_v2 != generation_v1
    run_contract_v2 = load_run_contract(state_dir)
    assert run_contract_v2 is not None
    assert run_contract_v2["workflow"]["proposal_digest"] == generation_v2
    semantic_replans = [
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.request.updated"
        and event.payload.get("revision_reason") == "semantic_replan"
    ]
    protocol_repairs = [
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.call.result.repair.requested"
    ]
    assert len(semantic_replans) == 1
    assert semantic_replans[0].causation_id == rejected_event.id
    assert semantic_replans[0].payload["attempt_domain"] == "gap"
    assert semantic_replans[0].payload["semantic_attempt_incremented"] is True
    assert len(protocol_repairs) == 1
    assert protocol_repairs[0].payload["semantic_attempt_incremented"] is False
    assert protocol_repairs[0].payload["repair_round"] == 1

    stale = _admit_delivery(
        state_dir=state_dir,
        writer=writer,
        operation_service=operation_service,
        invoke=invoke_v1,
        result=delivery_v1,
        prepared_reads=verify_v1_reads,
        event_id="evt-v1-verify-late",
    )
    assert stale.status == "superseded"
    assert "stale_workflow_generation" in {
        issue["code"] for issue in stale.issues
    }
    assert not any(
        event.type == "artifact.delivery.verified"
        and event.payload.get("workflow_generation") == generation_v1
        for event in writer.event_log.read_all()
    )

    requirement_v2 = {
        "ref": str(Path(request_v2["requirement_spec_ref"])),
        "sha256": request_v2["requirement_spec_digest"],
    }
    scope_v2_reads = _prepare_reads(
        state_dir,
        run_id=REQUEST_ID,
        attempt_id="attempt-v2-scope",
        role="scoper",
        sources=[_source(
            requirement_v2,
            source_id="requirement",
            artifact_id="requirement.json",
            kind="requirement/spec",
        )],
    )
    scope_v2 = _artifact(
        state_dir,
        root="workflow/artifacts/scope",
        kind="research/scope",
        schema_version="research-scope.v1",
        created_by="scoper",
        body={
            "schema_version": "research-scope.v1",
            "question": "Which two independent evidence families close the claim?",
        },
    )
    _append_stage_result(
        state_dir,
        writer,
        invoke=invoke_v2,
        stage_id="scope",
        role="scoper",
        output=scope_v2,
        ledger=scope_v2_reads["ledger"],
    )
    evidence_v2: list[dict[str, Any]] = []
    collect_v2_events: list[ZfEvent] = []
    for index, role in enumerate(("collector-a", "collector-b"), start=1):
        reads = _prepare_reads(
            state_dir,
            run_id=REQUEST_ID,
            attempt_id=f"attempt-v2-collect-{index}",
            role=role,
            sources=[_source(
                scope_v2,
                source_id="scope",
                artifact_id="scope.json",
                kind="research/scope",
            )],
        )
        evidence = _artifact(
            state_dir,
            root="workflow/artifacts/evidence",
            kind="research/evidence",
            schema_version="research-evidence.v1",
            created_by=role,
            body={
                "schema_version": "research-evidence.v1",
                "collector": role,
                "source_family": f"independent-family-{index}",
                "finding": f"corroborated-evidence-{index}",
            },
        )
        evidence_v2.append(evidence)
        stage = _append_stage_result(
            state_dir,
            writer,
            invoke=invoke_v2,
            stage_id=f"collect-{index}",
            role=role,
            output=evidence,
            ledger=reads["ledger"],
        )
        collect_v2_events.append(stage["event"])

    events_without_current_collect_2 = [
        event
        for event in writer.event_log.read_all()
        if event.id != collect_v2_events[1].id
    ]
    late_old_collect_2 = ZfEvent(
        id="evt-v1-collect-2-late",
        type="collect-2.completed",
        actor="collector-b",
        correlation_id=REQUEST_ID,
        payload={
            **_identity(invoke_v1),
            "stage_id": "collect-2",
            "role_instance": "collector-b",
        },
    )
    assert reconcile_dependency_barriers(
        config_v1,
        [*events_without_current_collect_2, late_old_collect_2],
    ) == []
    writer.append(late_old_collect_2)
    barrier_v2 = reconcile_dependency_barriers(
        load_config(config_path),
        writer.event_log.read_all(),
    )
    assert len(barrier_v2) == 1
    assert barrier_v2[0].payload["workflow_generation"] == generation_v2
    barrier_event_v2 = writer.append(barrier_v2[0].to_event())

    synth_transport = _RecordingTransport()
    restarted_for_synth = Orchestrator(
        state_dir,
        load_config(config_path),
        synth_transport,
        project_root=project_root,
    )
    restarted_for_synth.run_once(events=[barrier_event_v2])
    for _ in range(3):
        if synth_transport.sent:
            break
        restarted_for_synth.run_once(events=[])
    assert [item[0] for item in synth_transport.sent].count(
        "synthesizer"
    ) == 1
    duplicate_synth = _RecordingTransport()
    Orchestrator(
        state_dir,
        load_config(config_path),
        duplicate_synth,
        project_root=project_root,
    ).run_once(events=[barrier_event_v2])
    assert [item[0] for item in duplicate_synth.sent].count(
        "synthesizer"
    ) == 0

    synth_v2_reads = _prepare_reads(
        state_dir,
        run_id=REQUEST_ID,
        attempt_id="attempt-v2-synthesize",
        role="synthesizer",
        sources=[
            _source(
                evidence,
                source_id=f"collect-{index}",
                artifact_id=f"evidence-{index}.json",
                kind="research/evidence",
            )
            for index, evidence in enumerate(evidence_v2, start=1)
        ],
    )
    synth_operation = operation_service.ensure_operation(
        workflow_run_id=REQUEST_ID,
        operation_id=f"operation-synthesize-{generation_v2[:12]}",
        operation_type="fanout_reader_child",
        request={
            "attempt_domain": "plan",
            "workflow_generation": generation_v2,
            "input_refs": [
                evidence["ref"] for evidence in evidence_v2
            ],
        },
        parent_stage_id="synthesize",
        role_instance="synthesizer",
        correlation_id=REQUEST_ID,
    )
    replayed_synth = WorkflowOperationService(
        state_dir=state_dir,
        event_log=EventLog(state_dir / "events.jsonl"),
        event_writer=EventWriter(EventLog(state_dir / "events.jsonl")),
    ).ensure_operation(
        workflow_run_id=REQUEST_ID,
        operation_id=f"operation-synthesize-{generation_v2[:12]}",
        operation_type="fanout_reader_child",
        request={
            "attempt_domain": "plan",
            "workflow_generation": generation_v2,
            "input_refs": [
                evidence["ref"] for evidence in evidence_v2
            ],
        },
        parent_stage_id="synthesize",
        role_instance="synthesizer",
        correlation_id=REQUEST_ID,
    )
    assert synth_operation.created is True
    assert replayed_synth.replay_hit is True
    assert replayed_synth.request_hash == synth_operation.request_hash

    report_v2 = _artifact(
        state_dir,
        root="workflow/artifacts/reports",
        kind="report/markdown",
        schema_version="research-report.v1",
        created_by="synthesizer",
        body={
            "schema_version": "research-report.v1",
            "title": "Verified report",
            "findings": [
                "Independent source family one supports the conclusion.",
                "Independent source family two corroborates the conclusion.",
            ],
            **(dict(report_transform(2, {})) if report_transform else {}),
        },
    )
    synth_result_v2 = _append_stage_result(
        state_dir,
        writer,
        invoke=invoke_v2,
        stage_id="synthesize",
        role="synthesizer",
        output=report_v2,
        ledger=synth_v2_reads["ledger"],
    )
    synth_completed_v2 = synth_result_v2["event"]

    verify_transport = _RecordingTransport()
    restarted_for_verify = Orchestrator(
        state_dir,
        load_config(config_path),
        verify_transport,
        project_root=project_root,
    )
    restarted_for_verify.run_once(events=[synth_completed_v2])
    for _ in range(3):
        if verify_transport.sent:
            break
        restarted_for_verify.run_once(events=[])
    assert [item[0] for item in verify_transport.sent].count("verifier") == 1
    duplicate_verify = _RecordingTransport()
    Orchestrator(
        state_dir,
        load_config(config_path),
        duplicate_verify,
        project_root=project_root,
    ).run_once(events=[synth_completed_v2])
    assert [item[0] for item in duplicate_verify.sent].count("verifier") == 0

    verify_v2_reads = _prepare_reads(
        state_dir,
        run_id=REQUEST_ID,
        attempt_id="attempt-v2-verify",
        role="verifier",
        sources=[_source(
            report_v2,
            source_id="synthesize",
            artifact_id="report.json",
            kind="report/markdown",
        )],
        consume=provider_verifier is None,
    )
    binding = build_execution_binding(
        source_manifest=verify_v2_reads["manifest"],
        role_instance="verifier",
        provider_backend="mock",
    )
    context_envelope, context_envelope_ref = build_context_delivery_envelope(
        state_dir,
        source_manifest=verify_v2_reads["manifest"],
        source_manifest_descriptor=verify_v2_reads["manifest_ref"],
        workflow_run_id=REQUEST_ID,
        operation_id=f"operation-verify-{generation_v2[:12]}",
        attempt_id="attempt-v2-verify",
        dispatch_id="attempt-v2-verify",
        role_instance="verifier",
        provider_session_id="mock-session-after-restart",
        execution_binding=binding,
        previous_receipt_descriptor={
            "ref": "artifacts/context-delivery/receipts/missing.json",
            "sha256": "f" * 64,
        },
    )
    context_receipt = write_context_delivery_receipt(
        state_dir,
        envelope=context_envelope,
        envelope_descriptor=context_envelope_ref,
    )
    assert context_envelope["previous_state"] == "unknown"
    assert {
        section["delivery"] for section in context_envelope["sections"]
    } == {"full"}

    claim_set_v2, claim_event_v2 = _claim_set(state_dir, writer)
    expected_delivery_v2 = _delivery_result(
        invoke=invoke_v2,
        run_contract=run_contract_v2,
        claim_event=claim_event_v2,
        claim_set=claim_set_v2,
        report=report_v2,
        synthesis_envelope_ref=synth_result_v2["envelope_ref"],
        verdict="passed",
    )
    delivery_v2 = normalize_artifact_delivery_result(
        provider_verifier(
            project_root,
            state_dir,
            {
                "requirement": requirement_v2,
                "report": report_v2,
                "goal_claim_set": {
                    "ref": claim_event_v2.payload["goal_claim_set_ref"],
                    "sha256": claim_event_v2.payload[
                        "goal_claim_set_digest"
                    ],
                },
            },
            expected_delivery_v2,
        )
        if provider_verifier is not None
        else expected_delivery_v2
    )
    if provider_verifier is None:
        assert delivery_v2 == expected_delivery_v2
    else:
        assert str(delivery_v2.get("summary") or "").strip()
        assert {
            key: value
            for key, value in delivery_v2.items()
            if key != "summary"
        } == {
            key: value
            for key, value in expected_delivery_v2.items()
            if key != "summary"
        }
    if provider_verifier is not None:
        _consume_prepared_reads(
            state_dir,
            verify_v2_reads,
            role="verifier",
            provider=provider_backend,
        )
    admitted_v2 = _admit_delivery(
        state_dir=state_dir,
        writer=writer,
        operation_service=operation_service,
        invoke=invoke_v2,
        result=delivery_v2,
        prepared_reads=verify_v2_reads,
        event_id="evt-v2-verify-passed",
    )
    assert admitted_v2.admitted is True
    verify_operation = next(
        operation
        for operation in (
            operation_service.ensure_operation(
                workflow_run_id=REQUEST_ID,
                operation_id=f"operation-verify-{generation_v2[:12]}",
                operation_type="fanout_reader_child",
                request={
                    "attempt_domain": "plan",
                    "workflow_generation": generation_v2,
                    "request_revision": request_v2["revision"],
                    "generic_workflow_contract_digest": invoke_v2.payload[
                        "generic_workflow_contract_digest"
                    ],
                    "run_contract_ref": invoke_v2.payload["run_contract_ref"],
                    "run_contract_digest": invoke_v2.payload[
                        "run_contract_digest"
                    ],
                },
                parent_stage_id="verify",
                role_instance="verifier",
                correlation_id=REQUEST_ID,
            ),
        )
    )
    operation_service.mark_started(
        operation_id=verify_operation.operation_id,
        request_hash=verify_operation.request_hash,
        workflow_run_id=REQUEST_ID,
        dispatch_id="attempt-v2-verify",
        role_instance="verifier",
        active_attempt_id="attempt-v2-verify",
        provider_session_id="mock-session-after-restart",
        context_delivery_envelope_ref=context_envelope_ref,
        context_delivery_receipt_ref=context_receipt,
        correlation_id=REQUEST_ID,
    )
    verified = writer.append(ZfEvent(
        id="evt-v2-artifact-delivery-verified",
        type="artifact.delivery.verified",
        actor="zf-cli",
        correlation_id=REQUEST_ID,
        payload={
            **_identity(invoke_v2),
            "stage_id": "verify",
            "role_instance": "verifier",
            "admitted_call_result_ref": admitted_v2.envelope_ref,
            "control_result_ref": admitted_v2.control_result_ref,
            "artifact_delivery_result": delivery_v2,
            "context_delivery_receipt_ref": context_receipt,
        },
    ))
    _settle_mock_generation_fanouts(
        state_dir=state_dir,
        project_root=project_root,
        config_path=config_path,
        writer=writer,
        workflow_generation=generation_v2,
    )
    claim = run_goal_completion_claim_event(
        writer.event_log.read_all(),
        cause=verified,
    )
    assert claim is not None
    writer.append(claim)
    terminal = run_goal_completion_gate_event(
        writer.event_log.read_all(),
        claim=claim,
        run_contract=run_contract_v2,
    )
    assert terminal is not None
    assert terminal.type == "run.goal.completed"
    writer.append(terminal)
    assert run_goal_completion_gate_event(
        writer.event_log.read_all(),
        claim=claim,
        run_contract=run_contract_v2,
    ) is None

    final_events = writer.event_log.read_all()
    assert sum(
        event.type == "run.goal.completed" for event in final_events
    ) == 1
    receipt = build_goal_completion_receipt(
        final_events,
        run_id=REQUEST_ID,
        generated_at="2026-07-26T12:00:00+00:00",
    )
    dossier = build_goal_dossier(
        state_dir,
        REQUEST_ID,
        events=final_events,
    )
    assert receipt["artifact_delivery"]["required_artifacts"][0][
        "source_ref"
    ] == "synthesize.report"
    assert dossier["artifact_delivery"]["status"] == "ready"
    assert dossier["delivery_readiness"]["status"] == "ready"
    assert dossier["gaps"] == []

    query = ArtifactQueryService(
        state_dir=state_dir,
        project_root=project_root,
        config=load_config(config_path),
    )
    context = query.context(mode="canonical")
    delivery_rows = query.catalog_list(
        context=context,
        semantic_kind="artifact_delivery_result",
        run_id=REQUEST_ID,
        view="occurrences",
    )
    report_rows = query.catalog_list(
        context=context,
        kind="report/markdown",
        run_id=REQUEST_ID,
        view="occurrences",
    )
    assert any(
        row["stage_id"] == "verify"
        and row["event_id"]
        for row in delivery_rows["items"]
    )
    assert any(
        row["ref"] == report_v2["ref"]
        and row["event_id"]
        for row in report_rows["items"]
    )
    simulation = emit_simulation_done(
        terminal,
        events=writer.event_log.read_all(),
        writer=writer,
    )
    assert simulation is not None
    final_request = load_workflow_request(state_dir, REQUEST_ID)
    final_requirement = json.loads(
        Path(final_request["requirement_spec_ref"]).read_text(encoding="utf-8")
    )
    return {
        "schema_version": "generic-workflow-complex-scenario.v1",
        "workflow_run_id": REQUEST_ID,
        "workflow_generation": generation_v2,
        "effective_config_digest": proposal_v2["effective_config_ref"][
            "sha256"
        ],
        "run_contract_digest": run_contract_v2["contract_digest"],
        "completion_profile": "artifact_delivery",
        "objective": final_requirement["objective"],
        "stage_graph": [
            "scope",
            "collect-a",
            "collect-b",
            "synthesize",
            "verify",
        ],
        "request_revision": request_v2["revision"],
        "terminal_event_id": terminal.id,
        "simulation_event_id": simulation.id,
        "semantic_replan_count": len(semantic_replans),
        "protocol_repair_count": len(protocol_repairs),
        "oa": {
            "checkpoint_requested": sum(
                event.type == "orchestrator.semantic.checkpoint.requested"
                for event in final_events
            ),
            "decision_observed": sum(
                event.type == "orchestrator.semantic.decision.observed"
                for event in final_events
            ),
            "decision_applied": sum(
                event.type == "orchestrator.semantic.decision.applied"
                for event in final_events
            ),
            "provider_turns": sum(
                event.type == "workflow.operation.started"
                and str(event.payload.get("operation_id") or "")
                in {
                    str(candidate.payload.get("operation_id") or "")
                    for candidate in final_events
                    if candidate.type == "workflow.operation.requested"
                    and candidate.payload.get("operation_type")
                    == "orchestrator_agent_semantic"
                }
                for event in final_events
            ),
        },
        "required_artifact_refs": [
            item["ref"]
            for item in receipt["artifact_delivery"]["required_artifacts"]
        ],
        "dossier_status": dossier["delivery_readiness"]["status"],
        "request_status": final_request["status"],
    }


def test_generic_workflow_replans_once_and_closes_after_restart(
    tmp_path: Path,
) -> None:
    result = run_generic_workflow_complex_scenario(tmp_path)

    assert result["semantic_replan_count"] == 1
    assert result["protocol_repair_count"] == 1
    assert result["dossier_status"] == "ready"
    assert result["request_status"] == "running"
    assert result["oa"] == {
        "checkpoint_requested": 0,
        "decision_observed": 0,
        "decision_applied": 0,
        "provider_turns": 0,
    }


@pytest.mark.parametrize(
    "objective",
    [
        "Compare two independent delivery records and publish a verified report.",
        "比较两条独立交付记录，并发布一份经过验证的报告。",
    ],
    ids=["english", "chinese"],
)
def test_generic_workflow_preserves_bilingual_goal_across_parallel_lanes(
    tmp_path: Path,
    objective: str,
) -> None:
    result = run_generic_workflow_complex_scenario(
        tmp_path,
        objective=objective,
    )

    assert result["objective"] == objective
    assert result["stage_graph"] == [
        "scope",
        "collect-a",
        "collect-b",
        "synthesize",
        "verify",
    ]
    assert result["semantic_replan_count"] == 1
    assert result["dossier_status"] == "ready"
