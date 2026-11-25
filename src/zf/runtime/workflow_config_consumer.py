"""Admission fence between an approved config snapshot and its actual consumer."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.config.render import redact_config, renderable_config_to_primitive
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


def consumer_policy_digest(config: Any) -> str:
    body = _execution_fields(redact_config(renderable_config_to_primitive(config)))
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def approved_config_consumer_error(
    state_dir: Path, config: Any, payload: Mapping[str, Any],
) -> str:
    """Check new admission only; never replace a running consumer's config.

    Legacy invokes without a proposal retain their existing admission contract.
    Proposal integrity and approval must be checked by the caller first.
    """
    if not payload.get("workflow_proposal_ref"):
        return ""
    descriptor = payload.get("effective_config_ref")
    if not isinstance(descriptor, dict):
        return "consumer_config_snapshot_missing"
    try:
        hydrated = hydrate_sidecar_ref(state_dir, descriptor)
    except (SidecarRefError, OSError):
        return "consumer_config_snapshot_invalid"
    snapshot = hydrated.payload
    if not hydrated.ok or not isinstance(snapshot, dict):
        return "consumer_config_snapshot_invalid"
    expected = snapshot.get("config")
    if (snapshot.get("schema_version") != "effective-config-snapshot.v1"
            or not isinstance(expected, dict)):
        return "consumer_config_snapshot_invalid"
    current = redact_config(renderable_config_to_primitive(config))
    if _execution_fields(current) != _execution_fields(expected):
        return "consumer_config_stale: load the approved execution policy before new admission"
    return ""


def _execution_fields(config: Mapping[str, Any]) -> dict[str, Any]:
    # Both operands use the same rendered, redacted snapshot schema. UI and
    # telemetry changes cannot require another product-delivery approval.
    from zf.runtime.workflow_route_policy import EXECUTION_POLICY_FIELDS, EXECUTION_RUNTIME_FIELDS

    result = {name: config.get(name) for name in EXECUTION_POLICY_FIELDS}
    runtime = config.get("runtime") or {}
    result["runtime"] = {name: runtime.get(name) for name in EXECUTION_RUNTIME_FIELDS}
    return result


def workflow_proposal_binding_error(
    state_dir: Path,
    payload: dict[str, Any],
) -> str:
    proposal_ref = payload.get("workflow_proposal_ref")
    proposal_digest = str(
        payload.get("workflow_proposal_digest") or ""
    )
    if not isinstance(proposal_ref, dict) and not proposal_digest:
        return ""
    effective_ref = payload.get("effective_config_ref")
    effective_digest = str(payload.get("effective_config_digest") or "")
    run_contract_ref = str(payload.get("run_contract_ref") or "")
    run_contract_digest = str(payload.get("run_contract_digest") or "")
    request_id = str(payload.get("request_id") or "")
    if (
        not isinstance(proposal_ref, dict)
        or not proposal_digest
        or not isinstance(effective_ref, dict)
        or not effective_digest
        or not run_contract_ref
        or not run_contract_digest
        or not request_id
    ):
        return "approved proposal binding is incomplete"
    try:
        from zf.runtime.run_contract import load_run_contract_snapshot
        from zf.runtime.sidecar_refs import sidecar_path
        from zf.runtime.workflow_proposal import load_workflow_proposal
        from zf.runtime.workflow_requests import load_workflow_request

        request = load_workflow_request(state_dir, request_id)
        if (
            str(request.get("status") or "")
            not in {"submitted", "running"}
            or str(request.get("proposal_digest") or "")
            != proposal_digest
            or not _same_ref(
                request.get("proposal_ref"),
                proposal_ref,
            )
        ):
            return "workflow proposal is not approved for the current request"
        proposal = load_workflow_proposal(state_dir, proposal_ref)
        if (
            str(proposal.get("proposal_digest") or "")
            != proposal_digest
            or not _same_ref(
                proposal.get("effective_config_ref"),
                effective_ref,
            )
            or str(effective_ref.get("sha256") or "")
            != effective_digest
        ):
            return "workflow proposal effective config binding is invalid"
        snapshot_path = sidecar_path(state_dir, run_contract_ref)
        if snapshot_path.is_symlink():
            return "run contract snapshot is unsafe"
        raw = snapshot_path.read_bytes()
        snapshot = load_run_contract_snapshot(
            state_dir,
            {
                "ref": run_contract_ref,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "byte_count": len(raw),
            },
        )
        contract = snapshot["contract"]
        workflow = (
            contract.get("workflow")
            if isinstance(contract.get("workflow"), dict)
            else {}
        )
        config = (
            contract.get("config")
            if isinstance(contract.get("config"), dict)
            else {}
        )
        if (
            str(snapshot.get("contract_digest") or "")
            != run_contract_digest
            or str(workflow.get("proposal_digest") or "")
            != proposal_digest
            or not _same_ref(workflow.get("proposal_ref"), proposal_ref)
            or str(config.get("effective_snapshot_digest") or "")
            != effective_digest
            or not _same_ref(
                config.get("effective_snapshot_ref"),
                effective_ref,
            )
        ):
            return "run contract does not pin the approved proposal"
    except Exception:
        return "approved proposal binding cannot be verified"
    return ""


def _same_ref(left: Any, right: Any) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return (
        str(left.get("ref") or "") == str(right.get("ref") or "")
        and str(left.get("sha256") or "") == str(right.get("sha256") or "")
    )
