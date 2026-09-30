"""Generic Workflow v1 deterministic closure and replay E2E."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from zf.cli.flow import build_flow_intake, build_flow_submit_preview
from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_delivery_result import (
    normalize_artifact_delivery_result,
)
from zf.runtime.artifact_query.service import ArtifactQueryService
from zf.runtime.artifact_read_ledger import (
    build_attempt_source_manifest,
    build_input_consumption_policy,
    read_attempt_artifact,
    seal_read_ledger,
    validate_required_reads,
    write_attempt_source_manifest,
)
from zf.runtime.call_result_admission import CallResultAdmissionService
from zf.runtime.call_result_envelope import (
    normalize_call_result_envelope,
    write_immutable_json_sidecar,
)
from zf.runtime.context_delivery import (
    attach_context_sections,
    build_context_delivery_envelope,
    build_execution_binding,
    write_context_delivery_receipt,
)
from zf.runtime.generic_workflow_fanout import GENERIC_WORKFLOW_HANDOFF_KEYS
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.goal_completion_receipt import build_goal_completion_receipt
from zf.runtime.goal_dossier import build_goal_dossier
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_contract import load_run_contract
from zf.runtime.run_manager import (
    run_goal_completion_claim_event,
    run_goal_completion_gate_event,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_dependency_barrier import (
    SATISFIED_EVENT,
    reconcile_dependency_barriers,
)
from zf.runtime.workflow_operation import WorkflowOperationService
from zf.runtime.workflow_requests import (
    load_workflow_request,
    revise_workflow_request,
)
from zf.runtime.workflow_synthesis import (
    WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
    run_workflow_synthesis,
)


REQUEST_ID = "REQ-GENERIC-COMPLEX-MOCK"
ROLE_NAMES = (
    "scoper",
    "collector-a",
    "collector-b",
    "synthesizer",
    "verifier",
)


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str, object]] = []

    def send_task(
        self,
        role_name: str,
        briefing_path: Path,
        prompt: str,
        *,
        context: object = None,
    ) -> None:
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name: str) -> bool:
        return True

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""

    def poll_events(self) -> list[ZfEvent]:
        return []


def _write_base_config(path: Path) -> None:
    path.write_text(
        yaml.safe_dump_all(
            [{
                "apiVersion": "zaofu.dev/v1",
                "kind": "ZfConfig",
                "metadata": {"name": "generic-complex-mock"},
                "spec": {
                    "version": "1.0",
                    "project": {
                        "name": "generic-complex-mock",
                        "state_dir": ".zf",
                    },
                    "goal": {"enabled": True},
                    "roles": [
                        {
                            "name": role,
                            "instance_id": role,
                            "backend": "mock",
                            "role_kind": "reader",
                        }
                        for role in ROLE_NAMES
                    ],
                    "workflow": {
                        "execution_profiles": {
                            "direct-v1": {"strategy": "direct"},
                        },
                    },
                },
            }],
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _synthesis_candidate(request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
        "request_id": request["request_id"],
        "request_revision": request["revision"],
        "requirement_ref": request["requirement_spec_ref"],
        "requirement_digest": request["requirement_spec_digest"],
        "selected_flow_family": "Workflow",
        "short_flow_spec": {
            "flow_family": "Workflow",
            "intent": "research",
            "template": "evidence-synthesis-v1",
            "purpose": "Deliver a verified evidence synthesis.",
            "parameters": {
                "scoper_role": "scoper",
                "collector_roles": ["collector-a", "collector-b"],
                "synthesizer_role": "synthesizer",
                "verifier_role": "verifier",
                "artifact_name": "report",
                "artifact_kind": "report/markdown",
            },
        },
        "decision_rationale": "Use the registered evidence synthesis template.",
        "assumptions": [],
        "open_questions": [],
        "requested_roles": list(ROLE_NAMES),
        "requested_skills": [],
        "requested_profiles": ["direct-v1"],
        "completion_profile": {
            "id": "artifact_delivery",
            "delivery_policy": "report_only",
            "completion_threshold": "verified_artifacts",
            "required_artifacts": ["synthesize.report"],
        },
        "risk_hints": [],
    }


def _proposal(
    *,
    project_root: Path,
    state_dir: Path,
    config_path: Path,
    intake_ref: Path,
    manifest_ref: Path,
    writer: EventWriter,
    confirm: bool = False,
    acceptance: list[str] | None = None,
    revision_reason: str = "requirement_update",
    source_event_id: str = "",
    pattern_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    request = revise_workflow_request(
        state_dir,
        manifest_ref,
        actor="mock-operator",
        acceptance=acceptance,
        confirm=confirm,
        revision_reason=revision_reason,
        source_event_id=source_event_id,
        writer=writer,
    )
    synthesis = run_workflow_synthesis(
        state_dir=state_dir,
        project_root=project_root,
        config=load_config(config_path),
        writer=writer,
        request_id=REQUEST_ID,
        actor="mock-synthesis",
        candidate_result=_synthesis_candidate(request),
    )
    preview = build_flow_submit_preview(
        config_path=config_path,
        intake_path=intake_ref,
        flow_kind="workflow",
        requested_by="mock-operator",
        reason="generic workflow complex E2E",
        allow_missing_env=True,
        synthesis_result_ref=synthesis.result_ref,
        pattern_id=pattern_id,
    )
    assert preview["status"] in {"GO", "WARN"}
    assert preview["proposal"]["approval_status"] == "approvable"
    return request, synthesis.result, preview


def _execute_action(
    service: ControlledActionService,
    writer: EventWriter,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    requested = writer.append(ZfEvent(
        type="control.action.requested",
        actor="mock-operator",
        correlation_id=REQUEST_ID,
        payload={"action": action, **payload},
    ))
    return service.execute(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )


def _submit_proposal(
    *,
    service: ControlledActionService,
    writer: EventWriter,
    preview: Mapping[str, Any],
    intake_ref: Path,
) -> dict[str, Any]:
    proposal = dict(preview["proposal"])
    proposal_ref = dict(preview["proposal_ref"])
    if proposal["change_mode"] == "config_change":
        applied = _execute_action(
            service,
            writer,
            "workflow-config-apply",
            {
                "proposal_id": proposal["proposal_id"],
                "proposal_ref": proposal_ref,
                "proposal_digest": proposal["proposal_digest"],
                "validation_result_ref": proposal["validation_result_ref"],
                "approval_ref": "owner:generic-workflow-e2e",
                "idempotency_key": (
                    f"generic-apply:{proposal['proposal_digest']}"
                ),
            },
        )
        assert applied["ok"] is True, applied
    submitted = _execute_action(
        service,
        writer,
        "workflow-submit",
        {
            "request_id": REQUEST_ID,
            "intake_ref": str(intake_ref),
            "proposal_ref": proposal_ref,
            "proposal_digest": proposal["proposal_digest"],
            "kind": "workflow",
            "pattern_id": str(preview["payload"].get("pattern_id") or ""),
            "reason": "approved Generic Workflow proposal",
            "allow_missing_env": True,
        },
    )
    assert submitted["ok"] is True, submitted
    assert submitted["status"] == "accepted"
    return submitted


def _latest_event(
    writer: EventWriter,
    event_type: str,
) -> ZfEvent:
    return next(
        event
        for event in reversed(writer.event_log.read_all())
        if event.type == event_type
    )


def _identity(invoke: ZfEvent) -> dict[str, Any]:
    payload = invoke.payload
    return {
        key: payload[key]
        for key in dict.fromkeys((
            "workflow_run_id",
            "run_id",
            "request_id",
            "flow_kind",
            *GENERIC_WORKFLOW_HANDOFF_KEYS,
            "input_result_refs",
            "run_contract_ref",
            "run_contract_digest",
            "workflow_proposal_ref",
            "workflow_proposal_digest",
            "effective_config_ref",
            "effective_config_digest",
            "requirement_spec_ref",
            "requirement_spec_digest",
        ))
        if payload.get(key) not in (None, "", [], {})
    }


def _artifact(
    state_dir: Path,
    *,
    root: str,
    kind: str,
    schema_version: str,
    created_by: str,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    return write_immutable_json_sidecar(
        state_dir,
        dict(body),
        root=root,
        kind=kind,
        schema_version=schema_version,
        created_by=created_by,
    )


def _source(
    descriptor: Mapping[str, Any],
    *,
    source_id: str,
    artifact_id: str,
    kind: str,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "artifact_id": artifact_id,
        "kind": kind,
        "ref": str(descriptor["ref"]),
        "sha256": str(descriptor["sha256"]),
        "allowed_paths": ["$"],
    }


def _prepare_reads(
    state_dir: Path,
    *,
    run_id: str,
    attempt_id: str,
    role: str,
    sources: list[dict[str, Any]],
    consume: bool = True,
) -> dict[str, Any]:
    manifest = attach_context_sections(
        build_attempt_source_manifest(
            workflow_run_id=run_id,
            task_id="",
            attempt_id=attempt_id,
            dispatch_id=attempt_id,
            sources=sources,
            metadata={
                "read_purpose": "generic-workflow-stage-input",
            },
        ),
        output_profile_id="generic-workflow-read",
        explicit_required_reads=sources,
    )
    manifest_ref = write_attempt_source_manifest(state_dir, manifest)
    policy = build_input_consumption_policy(
        workflow_run_id=run_id,
        attempt_id=attempt_id,
        required_reads=[
            {
                "source_id": source["source_id"],
                "artifact_id": source["artifact_id"],
                "artifact_sha256": source["sha256"],
                "json_path": "$",
                "min_returned_bytes": 1,
            }
            for source in sources
        ],
    )
    if consume:
        for source in sources:
            read_attempt_artifact(
                state_dir,
                manifest=manifest,
                source_id=source["source_id"],
                artifact_id=source["artifact_id"],
                actor=role,
                role=role,
                provider="mock",
                purpose="generic-workflow-stage-input",
            )
        ledger = seal_read_ledger(state_dir, attempt_id)
        assert validate_required_reads(
            state_dir,
            policy=policy,
            ledger_descriptor=ledger,
        ) == []
        rows = [
            json.loads(line)
            for line in str(hydrate_sidecar_ref(state_dir, ledger).payload).splitlines()
            if line.strip()
        ]
        assert len(rows) == len(sources)
        assert {
            (
                row["consumer_actor"],
                row["consumer_role"],
                row["consumer_provider"],
            )
            for row in rows
        } == {(role, role, "mock")}
    else:
        ledger = {}
    return {
        "manifest": manifest,
        "manifest_ref": manifest_ref,
        "policy": policy,
        "ledger": ledger,
    }


def _consume_prepared_reads(
    state_dir: Path,
    prepared: Mapping[str, Any],
    *,
    role: str,
    provider: str = "mock",
) -> dict[str, Any]:
    manifest = prepared["manifest"]
    for source in manifest["sources"]:
        read_attempt_artifact(
            state_dir,
            manifest=manifest,
            source_id=source["source_id"],
            artifact_id=source["artifact_id"],
            actor=role,
            role=role,
            provider=provider,
            purpose="generic-workflow-stage-input",
        )
    ledger = seal_read_ledger(state_dir, str(manifest["attempt_id"]))
    assert validate_required_reads(
        state_dir,
        policy=prepared["policy"],
        ledger_descriptor=ledger,
    ) == []
    return ledger


def _append_stage_result(
    state_dir: Path,
    writer: EventWriter,
    *,
    invoke: ZfEvent,
    stage_id: str,
    role: str,
    output: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> dict[str, Any]:
    identity = _identity(invoke)
    control_ref = _artifact(
        state_dir,
        root=f"workflow/stage-results/{stage_id}",
        kind="generic_stage_result",
        schema_version="generic-stage-result.v1",
        created_by=role,
        body={
            "schema_version": "generic-stage-result.v1",
            "stage_id": stage_id,
            "status": "passed",
            "output_ref": dict(output),
        },
    )
    event_id = f"evt-{stage_id}-{str(identity['workflow_generation'])[:12]}"
    envelope = normalize_call_result_envelope(
        source_payload={
            **identity,
            "attempt_id": f"attempt-{stage_id}",
            "stage_id": stage_id,
            "role_instance": role,
            "read_ledger_ref": str(ledger.get("ref") or ""),
            "read_ledger_digest": str(ledger.get("sha256") or ""),
        },
        control_result={
            "schema_version": "generic-stage-result.v1",
            "ref": control_ref["ref"],
            "sha256": control_ref["sha256"],
        },
        workflow_run_id=str(identity["workflow_run_id"]),
        operation_id=f"operation-{stage_id}",
        request_hash=hashlib.sha256(
            f"{identity['workflow_generation']}:{stage_id}".encode()
        ).hexdigest(),
        source_event_id=event_id,
        source_event_type=f"{stage_id}.completed",
        actor=role,
        correlation_id=str(identity["workflow_run_id"]),
    )
    envelope_ref = write_immutable_json_sidecar(
        state_dir,
        envelope,
        root="call-results/envelopes",
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="generic-workflow-e2e",
        source_event_id=event_id,
    )
    writer.append(ZfEvent(
        id=f"{event_id}-admitted",
        type="workflow.call.result.admitted",
        actor="zf-cli",
        correlation_id=str(identity["workflow_run_id"]),
        payload={
            **identity,
            "stage_id": stage_id,
            "role_instance": role,
            "operation_id": f"operation-{stage_id}",
            "envelope_ref": envelope_ref,
            "control_result_ref": control_ref,
            "read_ledger_ref": dict(ledger),
        },
    ))
    completed = writer.append(ZfEvent(
        id=event_id,
        type=f"{stage_id}.completed",
        actor=role,
        correlation_id=str(identity["workflow_run_id"]),
        payload={
            **identity,
            "stage_id": stage_id,
            "role_instance": role,
            "output_ref": dict(output),
            "admitted_call_result_ref": envelope_ref,
            "input_result_refs": [str(envelope_ref["ref"])],
            "read_ledger_ref": dict(ledger),
        },
    ))
    return {
        "event": completed,
        "envelope_ref": envelope_ref,
        "control_ref": control_ref,
    }


def _claim_set(
    state_dir: Path,
    writer: EventWriter,
) -> tuple[dict[str, Any], ZfEvent]:
    event = _latest_event(writer, "goal.claim_set.pinned")
    body = event.payload
    claim_set = hydrate_sidecar_ref(
        state_dir,
        {
            "ref": body["goal_claim_set_ref"],
            "sha256": body["goal_claim_set_digest"],
        },
    ).payload
    assert isinstance(claim_set, dict)
    return claim_set, event


def _delivery_result(
    *,
    invoke: ZfEvent,
    run_contract: Mapping[str, Any],
    claim_event: ZfEvent,
    claim_set: Mapping[str, Any],
    report: Mapping[str, Any],
    synthesis_envelope_ref: Mapping[str, Any],
    verdict: str,
    gap_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    identity = _identity(invoke)
    closed = verdict == "passed"
    return normalize_artifact_delivery_result({
        "schema_version": "artifact-delivery-result.v1",
        "workflow_run_id": identity["workflow_run_id"],
        "goal_id": identity["goal_id"],
        "workflow_generation": identity["workflow_generation"],
        "request_revision": identity["request_revision"],
        "generic_workflow_contract_digest": identity[
            "generic_workflow_contract_digest"
        ],
        "run_contract_ref": identity["run_contract_ref"],
        "run_contract_digest": run_contract["contract_digest"],
        "completion_profile": "artifact_delivery",
        "goal_claim_set_ref": claim_event.payload["goal_claim_set_ref"],
        "goal_claim_set_digest": claim_event.payload[
            "goal_claim_set_digest"
        ],
        "verifier_stage_id": "verify",
        "verifier_role": "verifier",
        "artifacts": [{
            **dict(report),
            "name": "report",
            "kind": "report/markdown",
            "source_ref": "synthesize.report",
            "producer_stage_id": "synthesize",
        }],
        "goal_coverage": [
            {
                "goal_claim_id": claim["goal_claim_id"],
                "status": "closed" if closed else "open",
                "supporting_artifact_refs": (
                    [report["ref"]] if closed else []
                ),
            }
            for claim in claim_set["claims"]
        ],
        "input_result_refs": [synthesis_envelope_ref["ref"]],
        "verification_evidence_refs": [
            str((gap_ref or report)["ref"]),
        ],
        "open_gap_refs": [gap_ref["ref"]] if gap_ref else [],
        "verdict": verdict,
        "recommended_action": "complete" if closed else "replan",
        "summary": (
            "All mandatory claims are independently verified."
            if closed
            else "A mandatory source-diversity claim remains open."
        ),
    })


def _admit_delivery(
    *,
    state_dir: Path,
    writer: EventWriter,
    operation_service: WorkflowOperationService,
    invoke: ZfEvent,
    result: Mapping[str, Any],
    prepared_reads: Mapping[str, Any],
    event_id: str,
) -> Any:
    identity = _identity(invoke)
    operation = operation_service.ensure_operation(
        workflow_run_id=str(identity["workflow_run_id"]),
        operation_id=f"operation-verify-{identity['workflow_generation'][:12]}",
        operation_type="fanout_reader_child",
        request={
            "attempt_domain": "plan",
            "workflow_generation": identity["workflow_generation"],
            "request_revision": identity["request_revision"],
            "generic_workflow_contract_digest": identity[
                "generic_workflow_contract_digest"
            ],
            "run_contract_ref": identity["run_contract_ref"],
            "run_contract_digest": identity["run_contract_digest"],
        },
        parent_stage_id="verify",
        role_instance="verifier",
        correlation_id=str(identity["workflow_run_id"]),
    )
    admission = CallResultAdmissionService(
        state_dir=state_dir,
        event_log=writer.event_log,
        event_writer=writer,
        operation_service=operation_service,
    )
    return admission.report_legacy_result(
        ZfEvent(
            id=event_id,
            type="verify.child.completed",
            actor="verifier",
            correlation_id=str(identity["workflow_run_id"]),
            payload={
                **identity,
                "attempt_id": str(
                    prepared_reads["manifest"]["attempt_id"]
                ),
                "attempt_domain": "plan",
                "stage_id": "verify",
                "role_instance": "verifier",
                "artifact_delivery_result": dict(result),
            },
        ),
        mode="blocking",
        operation={
            "workflow_run_id": identity["workflow_run_id"],
            "operation_id": operation.operation_id,
            "request_hash": operation.request_hash,
        },
        input_policy=prepared_reads["policy"],
    )


def _identity_payload_from_manifest(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    trigger_payload = (
        manifest.get("trigger_payload")
        if isinstance(manifest.get("trigger_payload"), dict)
        else {}
    )
    return {
        key: value
        for key in dict.fromkeys((
            "workflow_run_id",
            *GENERIC_WORKFLOW_HANDOFF_KEYS,
            "input_result_refs",
            "run_contract_ref",
            "run_contract_digest",
        ))
        if (value := manifest.get(key) or trigger_payload.get(key))
        not in (None, "")
    }


def _settle_mock_generation_fanouts(
    *,
    state_dir: Path,
    project_root: Path,
    config_path: Path,
    writer: EventWriter,
    workflow_generation: str,
) -> None:
    started = [
        event
        for event in writer.event_log.read_all()
        if event.type == "fanout.started"
        and isinstance(event.payload, dict)
        and str(
            event.payload.get("workflow_generation")
            or (
                event.payload.get("trigger_payload", {}).get(
                    "workflow_generation"
                )
                if isinstance(event.payload.get("trigger_payload"), dict)
                else ""
            )
        ) == workflow_generation
    ]
    runtime = Orchestrator(
        state_dir,
        load_config(config_path),
        _RecordingTransport(),
        project_root=project_root,
    )
    for started_event in started:
        fanout_id = str(started_event.payload.get("fanout_id") or "")
        manifest_path = state_dir / "fanouts" / fanout_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        aggregate = (
            manifest.get("aggregate")
            if isinstance(manifest.get("aggregate"), dict)
            else {}
        )
        if str(aggregate.get("status") or "") in {
            "completed",
            "failed",
            "timed_out",
            "cancelled",
        }:
            continue
        stage_id = str(manifest.get("stage_id") or "")
        stage_results = [
            event
            for event in writer.event_log.read_all()
            if isinstance(event.payload, dict)
            and str(event.payload.get("workflow_generation") or "")
            == workflow_generation
            and (
                str(event.payload.get("stage_id") or "") == stage_id
                or (
                    stage_id == "verify"
                    and event.type == "artifact.delivery.verified"
                )
            )
            and (
                event.type == f"{stage_id}.completed"
                or event.type == "artifact.delivery.verified"
            )
        ]
        source_event = stage_results[-1] if stage_results else started_event
        source_payload = (
            source_event.payload
            if isinstance(source_event.payload, dict)
            else {}
        )
        for child in manifest.get("children", []) or []:
            if not isinstance(child, dict):
                continue
            if str(child.get("status") or "") in {
                "completed",
                "failed",
                "timed_out",
                "cancelled",
            }:
                continue
            writer.append(ZfEvent(
                type="fanout.child.completed",
                actor=str(child.get("role_instance") or "mock-provider"),
                correlation_id=str(manifest.get("trace_id") or ""),
                causation_id=source_event.id,
                payload={
                    **_identity_payload_from_manifest(manifest),
                    "fanout_id": fanout_id,
                    "trace_id": str(manifest.get("trace_id") or ""),
                    "stage_id": stage_id,
                    "child_id": str(child.get("child_id") or ""),
                    "run_id": str(child.get("run_id") or ""),
                    "role_instance": str(child.get("role_instance") or ""),
                    "status": "completed",
                    "result_event_id": source_event.id,
                    "operation_id": str(child.get("operation_id") or ""),
                    "request_hash": str(child.get("request_hash") or ""),
                    "admitted_call_result_ref": source_payload.get(
                        "admitted_call_result_ref",
                        {},
                    ),
                    "control_result_ref": source_payload.get(
                        "control_result_ref",
                        {},
                    ),
                },
            ))
        runtime._evaluate_reader_fanout(fanout_id)
