"""Durable owner checkpoint for typed Plan synthesis decisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_preview_artifacts import (
    approval_artifact_refs,
    write_plan_preview_bundle,
)
from zf.runtime.plan_synth_feedback import (
    normalize_plan_synth_owner_decision_items,
)


OWNER_CHECKPOINT_REQUESTED = "plan.synth.owner_decision.requested"
OWNER_CHECKPOINT_RESOLVED = "plan.synth.owner_decision.resolved"


@dataclass(frozen=True)
class PlanSynthOwnerDisposition:
    hold: bool
    payload: dict[str, Any]
    checkpoint_id: str = ""


def apply_plan_synth_owner_checkpoint(
    runtime: Any,
    *,
    event: ZfEvent,
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> PlanSynthOwnerDisposition:
    """Hold an unresolved typed owner question, or bind its resolution."""

    body = dict(payload)
    report = body.get("report") if isinstance(body.get("report"), Mapping) else {}
    items = normalize_plan_synth_owner_decision_items(
        body.get("owner_decision_items") or report.get("owner_decision_items")
    )
    blocking_items = [item for item in items if item.get("blocking") is not False]
    if not blocking_items:
        return PlanSynthOwnerDisposition(False, body)

    fanout_id = str(body.get("fanout_id") or manifest.get("fanout_id") or "")
    workflow_run_id = str(
        body.get("workflow_run_id")
        or manifest.get("workflow_run_id")
        or manifest.get("trace_id")
        or event.correlation_id
        or ""
    )
    checkpoint_id = _checkpoint_id(
        fanout_id=fanout_id,
        source_event_id=event.id,
        decision_ids=[str(item["decision_id"]) for item in blocking_items],
    )
    events = runtime.event_log.read_all()
    requested = _checkpoint_request(events, checkpoint_id)
    if requested is None:
        descriptor = write_immutable_json_sidecar(
            runtime.state_dir,
            {
                "schema_version": "plan-synth-owner-checkpoint.v1",
                "checkpoint_id": checkpoint_id,
                "workflow_run_id": workflow_run_id,
                "fanout_id": fanout_id,
                "stage_id": str(body.get("stage_id") or manifest.get("stage_id") or ""),
                "source_event_id": event.id,
                "owner_decision_items": blocking_items,
                "control_result_ref": dict(body.get("control_result_ref") or {}),
            },
            root="plan-synth/owner-checkpoints",
            kind="plan_synth_owner_checkpoint",
            schema_version="plan-synth-owner-checkpoint.v1",
            created_by="plan-synth-owner-checkpoint",
            source_event_id=event.id,
        )
        proposal_ref = str(descriptor.get("ref") or "")
        eval_ref = str(
            (body.get("control_result_ref") or {}).get("ref")
            if isinstance(body.get("control_result_ref"), Mapping)
            else ""
        ) or f"events:{event.id}"
        requested = runtime.event_writer.append(ZfEvent(
            type=OWNER_CHECKPOINT_REQUESTED,
            actor="zf-cli",
            origin="kernel",
            task_id=event.task_id,
            payload={
                "schema_version": "plan-synth-owner-checkpoint-request.v1",
                "checkpoint_id": checkpoint_id,
                "workflow_run_id": workflow_run_id,
                "fanout_id": fanout_id,
                "stage_id": str(body.get("stage_id") or manifest.get("stage_id") or ""),
                "source_event_id": event.id,
                "proposal_ref": proposal_ref,
                "eval_ref": eval_ref,
                "candidate_task_map_ref": str(body.get("task_map_ref") or ""),
                "owner_decision_ref": descriptor,
                "owner_decision_items": blocking_items,
            },
            causation_id=event.id,
            correlation_id=workflow_run_id or event.correlation_id,
        ))
        first = blocking_items[0]
        preview_refs = plan_preview_refs(
            body,
            state_dir=runtime.state_dir,
            project_root=getattr(runtime, "project_root", None),
        )
        preview_bundle = write_plan_preview_bundle(
            runtime.state_dir,
            payload=body,
            refs=preview_refs,
            project_root=getattr(runtime, "project_root", None),
            source_event_id=event.id,
        )
        runtime.event_writer.append(ZfEvent(
            type="approval.requested",
            actor="zf-cli",
            origin="kernel",
            task_id=event.task_id,
            payload={
                "schema_version": "approval.requested.v1",
                "approval_ref": checkpoint_id,
                "source_role": "orchestrator",
                "owner_route": "plan_synth_owner_decision",
                "title": "Plan requires owner decision",
                "summary": str(first.get("question") or "Owner decision required"),
                "checkpoint_id": checkpoint_id,
                "workflow_run_id": workflow_run_id,
                "fanout_id": fanout_id,
                "source_event_id": event.id,
                "proposal_ref": proposal_ref,
                "eval_ref": eval_ref,
                "candidate_task_map_ref": str(
                    body.get("task_map_ref") or preview_refs.get("task_map_ref") or ""
                ),
                # The owner checkpoint is the first human stop in a PRD Plan
                # flow.  Carry the immutable candidate refs here so the
                # operator can inspect the complete Plan before answering the
                # questions.  This remains a generic approval request: it
                # does not mint canonical tasks or unlock writer fanout.
                "plan_id": event.id,
                "plan_preview": True,
                "plan_artifact_ref": str(preview_refs.get("plan_artifact_ref") or ""),
                "plan_artifact_digest": str(preview_refs.get("plan_artifact_digest") or ""),
                "task_map_ref": str(preview_refs.get("task_map_ref") or ""),
                "source_index_ref": str(preview_refs.get("source_index_ref") or ""),
                "plan_preview_bundle_ref": str(preview_bundle.get("ref") or ""),
                "plan_preview_bundle_digest": str(preview_bundle.get("sha256") or ""),
                "plan_content_profile": str(body.get("plan_content_profile") or "derived_plan"),
                "authority_refs": list(body.get("authority_refs") or []),
                "requirement_spec_ref": str(body.get("requirement_spec_ref") or ""),
                "owner_decision_items": blocking_items,
                "approve_action": "replan-approve",
                "reject_action": "replan-reject",
            },
            causation_id=requested.id,
            correlation_id=workflow_run_id or event.correlation_id,
        ))
        return PlanSynthOwnerDisposition(True, body, checkpoint_id)

    request_payload = (
        requested.payload if isinstance(requested.payload, dict) else {}
    )
    decision = _owner_decision(
        events,
        checkpoint_id=checkpoint_id,
        proposal_ref=str(request_payload.get("proposal_ref") or ""),
    )
    if decision is None or decision.type.endswith(".deferred"):
        return PlanSynthOwnerDisposition(True, body, checkpoint_id)

    resolution = decision.type.rsplit(".", 1)[-1]
    confirmation = write_immutable_json_sidecar(
        runtime.state_dir,
        {
            "schema_version": "plan-synth-owner-confirmation.v1",
            "checkpoint_id": checkpoint_id,
            "workflow_run_id": workflow_run_id,
            "fanout_id": fanout_id,
            "source_event_id": event.id,
            "decision_event_id": decision.id,
            "decision": resolution,
            "owner_decision_items": blocking_items,
            "response": dict(decision.payload or {}),
        },
        root="plan-synth/owner-confirmations",
        kind="plan_synth_owner_confirmation",
        schema_version="plan-synth-owner-confirmation.v1",
        created_by="plan-synth-owner-checkpoint",
        source_event_id=decision.id,
    )
    if not _checkpoint_resolution(events, checkpoint_id, decision.id):
        runtime.event_writer.append(ZfEvent(
            type=OWNER_CHECKPOINT_RESOLVED,
            actor="zf-cli",
            origin="kernel",
            task_id=event.task_id,
            payload={
                "schema_version": "plan-synth-owner-checkpoint-resolution.v1",
                "checkpoint_id": checkpoint_id,
                "workflow_run_id": workflow_run_id,
                "fanout_id": fanout_id,
                "source_event_id": event.id,
                "decision_event_id": decision.id,
                "decision": resolution,
                "owner_confirmation_ref": confirmation,
            },
            causation_id=decision.id,
            correlation_id=workflow_run_id or event.correlation_id,
        ))

    augmented_report = dict(report)
    augmented_report["owner_decision_items"] = blocking_items
    augmented_report["owner_confirmation_ref"] = str(confirmation.get("ref") or "")
    augmented_report["owner_decision_resolution"] = resolution
    findings = [
        dict(item)
        for item in augmented_report.get("findings", [])
        if isinstance(item, Mapping)
    ]
    findings.append({
        "severity": "high",
        "category": "owner_decision_resolved",
        "message": (
            f"Owner decision {checkpoint_id} resolved as {resolution}; "
            "the next Plan revision must bind the confirmation artifact."
        ),
        "required_change": "Apply the owner response without widening scope beyond it.",
        "evidence_refs": [str(confirmation.get("ref") or "")],
    })
    augmented_report["findings"] = findings
    refs = [
        dict(item)
        for item in body.get("previous_plan_candidate_refs", [])
        if isinstance(item, Mapping)
    ]
    if not any(str(item.get("ref") or "") == confirmation.get("ref") for item in refs):
        refs.append({
            **confirmation,
            "source_id": "owner-confirmation",
            "artifact_id": "owner-confirmation.json",
            "allowed_paths": ["$"],
        })
    body.update({
        "report": augmented_report,
        "findings": findings,
        "owner_decision_items": blocking_items,
        "owner_confirmation_ref": str(confirmation.get("ref") or ""),
        "owner_confirmation": confirmation,
        "owner_decision_resolution": resolution,
        "previous_plan_candidate_refs": refs,
    })
    return PlanSynthOwnerDisposition(False, body, checkpoint_id)


def plan_preview_refs(
    body: Mapping[str, Any],
    *,
    state_dir: Any,
    project_root: Any,
) -> dict[str, Any]:
    """Resolve Planner artifact refs for the owner checkpoint preview.

    The Plan Critic may reject on an owner decision before it can publish the
    canonical ``task_map.ready`` payload.  In that case the candidate refs are
    carried in ``previous_plan_candidate_refs``.  Select only the named Plan,
    task map, and source index artifacts; arbitrary child-result JSON is not a
    valid preview body.
    """

    refs: dict[str, Any] = {}
    try:
        refs.update(approval_artifact_refs(
            body,
            state_dir=state_dir,
            project_root=project_root,
        ))
    except (OSError, ValueError):
        refs.update({
            key: body.get(key)
            for key in (
                "plan_artifact_ref", "plan_artifact_digest", "plan_ref",
                "task_map_ref", "source_index_ref",
            )
            if body.get(key)
        })
    for key in ("task_map_ref", "source_index_ref"):
        if body.get(key) and not refs.get(key):
            refs[key] = body[key]
    for key in ("plan_content_profile", "authority_refs", "requirement_spec_ref"):
        if body.get(key) and not refs.get(key):
            refs[key] = body[key]

    descriptors = body.get("previous_plan_candidate_refs")
    if not isinstance(descriptors, list):
        descriptors = []
    for descriptor in descriptors:
        if not isinstance(descriptor, Mapping):
            continue
        ref = str(descriptor.get("ref") or "").strip()
        if not ref:
            continue
        artifact_id = str(
            descriptor.get("artifact_id") or descriptor.get("source_id") or ref
        ).strip().lower()
        name = ref.rsplit("/", 1)[-1].lower()
        digest = str(
            descriptor.get("sha256") or descriptor.get("digest") or ""
        ).strip()
        if name == "task_map.json" or "task_map" in artifact_id:
            refs.setdefault("task_map_ref", ref)
            if digest:
                refs.setdefault("task_map_digest", digest)
        elif name == "source_index.json" or "source_index" in artifact_id:
            refs.setdefault("source_index_ref", ref)
        elif name.endswith((".md", ".markdown")) and not refs.get("plan_artifact_ref"):
            refs["plan_artifact_ref"] = ref
            if digest:
                refs["plan_artifact_digest"] = digest
    return refs


def _checkpoint_id(
    *,
    fanout_id: str,
    source_event_id: str,
    decision_ids: list[str],
) -> str:
    digest = hashlib.sha256(
        "\x1f".join((fanout_id, source_event_id, *decision_ids)).encode("utf-8")
    ).hexdigest()[:16]
    return f"plan-owner-{digest}"


def _checkpoint_request(events: list[ZfEvent], checkpoint_id: str) -> ZfEvent | None:
    return next((
        event
        for event in reversed(events)
        if event.type == OWNER_CHECKPOINT_REQUESTED
        and isinstance(event.payload, dict)
        and str(event.payload.get("checkpoint_id") or "") == checkpoint_id
    ), None)


def _checkpoint_resolution(
    events: list[ZfEvent],
    checkpoint_id: str,
    decision_event_id: str,
) -> bool:
    return any(
        event.type == OWNER_CHECKPOINT_RESOLVED
        and isinstance(event.payload, dict)
        and str(event.payload.get("checkpoint_id") or "") == checkpoint_id
        and str(event.payload.get("decision_event_id") or "") == decision_event_id
        for event in events
    )


def _owner_decision(
    events: list[ZfEvent],
    *,
    checkpoint_id: str,
    proposal_ref: str,
) -> ZfEvent | None:
    for event in reversed(events):
        if not event.type.startswith("replan.owner_decision."):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("checkpoint_id") or "") == checkpoint_id
            or str(payload.get("approval_ref") or "") == checkpoint_id
            or (
                proposal_ref
                and str(payload.get("proposal_ref") or "") == proposal_ref
            )
        ):
            return event
    return None


__all__ = [
    "OWNER_CHECKPOINT_REQUESTED",
    "OWNER_CHECKPOINT_RESOLVED",
    "PlanSynthOwnerDisposition",
    "apply_plan_synth_owner_checkpoint",
]
