"""Typed recovery proposals and mechanical diagnosis/action compatibility."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


RECOVERY_PROPOSAL_SCHEMA = "recovery-proposal.v1"

_REPLAN_CLASSIFICATIONS = frozenset({
    "design_issue",
    "contract_issue",
    "task_contract_unsatisfiable",
    "stale_artifact",
    "semantic_replan_artifact_required",
})
_REPLAN_ACTIONS = frozenset({
    "replan",
    "request_replan",
    "orchestrator_replan",
    "orchestrator.replan_requested",
})
_SAME_CONTRACT_ACTIONS = frozenset({
    "repair_failed_children",
    "trigger_rework",
    "resume",
    "respawn",
})


class RecoveryDeltaError(ValueError):
    """Recovery action cannot satisfy the diagnosed semantic delta."""


def build_recovery_proposal(
    *,
    problem_ref: str,
    failure_scope: str,
    classification: str,
    recommended_action: str,
    affected_task_ids: Iterable[str],
    current_package: Mapping[str, Any] | None = None,
    run_contract_ref: str = "",
    run_contract_digest: str = "",
    required_delta: Mapping[str, Any] | None = None,
    expected_resolution: str = "",
    verify_condition: str = "",
) -> dict[str, Any]:
    package = current_package or {}
    proposal = {
        "schema_version": RECOVERY_PROPOSAL_SCHEMA,
        "problem_ref": problem_ref,
        "failure_scope": failure_scope,
        "classification": classification,
        "current_plan_artifact_package_id": str(package.get("package_id") or ""),
        "current_plan_artifact_package_ref": str(package.get("package_ref") or ""),
        "current_plan_artifact_package_digest": str(
            package.get("package_digest") or ""
        ),
        "run_contract_ref": run_contract_ref,
        "run_contract_digest": run_contract_digest,
        "recommended_action": recommended_action,
        "affected_task_ids": list(dict.fromkeys(
            str(item).strip() for item in affected_task_ids if str(item).strip()
        )),
        "required_delta": dict(required_delta or {}),
        "expected_resolution": expected_resolution,
        "verify_condition": verify_condition,
    }
    validate_recovery_proposal(proposal)
    return proposal


def validate_recovery_proposal(proposal: Mapping[str, Any]) -> None:
    if proposal.get("schema_version") != RECOVERY_PROPOSAL_SCHEMA:
        raise RecoveryDeltaError("unsupported recovery proposal schema")
    for key in (
        "problem_ref",
        "failure_scope",
        "classification",
        "recommended_action",
        "expected_resolution",
        "verify_condition",
    ):
        if not str(proposal.get(key) or ""):
            raise RecoveryDeltaError(f"{key} is required")
    classification = str(proposal.get("classification") or "")
    action = str(proposal.get("recommended_action") or "")
    if classification in _REPLAN_CLASSIFICATIONS and action in _SAME_CONTRACT_ACTIONS:
        raise RecoveryDeltaError(
            f"{classification} cannot use unchanged-contract action {action}"
        )


def write_recovery_proposal(
    state_dir: Path,
    proposal: Mapping[str, Any],
    *,
    source_event_id: str = "",
) -> dict[str, Any]:
    validate_recovery_proposal(proposal)
    return write_immutable_json_sidecar(
        state_dir,
        proposal,
        root="recovery/proposals",
        kind="recovery_proposal",
        schema_version=RECOVERY_PROPOSAL_SCHEMA,
        created_by="run-manager",
        source_event_id=source_event_id,
    )


def hydrate_recovery_proposal(
    state_dir: Path,
    descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    hydrated = hydrate_sidecar_ref(state_dir, dict(descriptor))
    if not isinstance(hydrated.payload, dict):
        raise RecoveryDeltaError("recovery proposal must be a JSON object")
    proposal = dict(hydrated.payload)
    validate_recovery_proposal(proposal)
    return proposal


def validate_recovery_action(
    proposal: Mapping[str, Any],
    *,
    action: str,
    new_package_digest: str = "",
    postcondition_receipt: Mapping[str, Any] | None = None,
) -> None:
    validate_recovery_proposal(proposal)
    classification = str(proposal.get("classification") or "")
    if classification in _REPLAN_CLASSIFICATIONS and action in _SAME_CONTRACT_ACTIONS:
        raise RecoveryDeltaError(
            f"{classification} requires semantic replan, got {action}"
        )
    old_digest = str(
        proposal.get("current_plan_artifact_package_digest") or ""
    )
    required_delta = proposal.get("required_delta")
    required_delta = required_delta if isinstance(required_delta, Mapping) else {}
    requires_revision = bool(required_delta.get("task_map_revision_required"))
    if (
        action in _REPLAN_ACTIONS
        and requires_revision
        and new_package_digest
        and new_package_digest == old_digest
    ):
        raise RecoveryDeltaError("semantic replan did not change the plan package")
    if postcondition_receipt is not None:
        if str(postcondition_receipt.get("status") or "") not in {
            "passed",
            "satisfied",
        }:
            raise RecoveryDeltaError("recovery postcondition is not satisfied")


def attach_recovery_proposal(
    action: Mapping[str, Any],
    *,
    state_dir: Path,
    events: list[ZfEvent],
) -> dict[str, Any]:
    updated = dict(action)
    recommendation = str(
        updated.get("recommended_action")
        or updated.get("safe_resume_action")
        or updated.get("action")
        or ""
    )
    classification = str(
        updated.get("classification")
        or updated.get("failure_class")
        or "worker_lifecycle"
    )
    run_id = str(
        updated.get("workflow_run_id")
        or updated.get("run_id")
        or updated.get("pdd_id")
        or updated.get("feature_id")
        or ""
    )
    package = (
        reduce_plan_artifact_packages(events, workflow_run_id=run_id).get("current")
        if run_id
        else {}
    )
    package = package if isinstance(package, Mapping) else {}
    required_delta = (
        {
            "contract_fields": ["acceptance_criteria.evidence_requirements"],
            "ownership_paths": list(updated.get("ownership_paths") or []),
            "task_map_revision_required": True,
        }
        if classification in _REPLAN_CLASSIFICATIONS
        else {}
    )
    proposal = build_recovery_proposal(
        problem_ref=str(
            updated.get("source_event_id")
            or updated.get("recorded_event_id")
            or updated.get("checkpoint_id")
            or ""
        ),
        failure_scope=str(updated.get("failure_scope") or "task"),
        classification=classification,
        recommended_action=recommendation,
        affected_task_ids=[
            str(updated.get("task_id") or ""),
            *list(updated.get("failed_task_ids") or []),
        ],
        current_package=package,
        run_contract_ref=str(updated.get("run_contract_ref") or ""),
        run_contract_digest=str(updated.get("run_contract_digest") or ""),
        required_delta=required_delta,
        expected_resolution=str(
            updated.get("guidance")
            or updated.get("summary")
            or "recovery action restores forward progress"
        ),
        verify_condition=str(
            updated.get("verify_condition")
            or "expected_downstream_event:task_map.ready"
        ),
    )
    descriptor = write_recovery_proposal(
        state_dir,
        proposal,
        source_event_id=str(updated.get("source_event_id") or ""),
    )
    updated.update({
        "recovery_proposal_ref": str(descriptor.get("ref") or ""),
        "recovery_proposal_digest": str(descriptor.get("sha256") or ""),
    })
    return updated


__all__ = [
    "RECOVERY_PROPOSAL_SCHEMA",
    "RecoveryDeltaError",
    "attach_recovery_proposal",
    "build_recovery_proposal",
    "hydrate_recovery_proposal",
    "validate_recovery_action",
    "validate_recovery_proposal",
    "write_recovery_proposal",
]
