"""Thin, immutable call-result envelope helpers.

The envelope binds one provider execution to one workflow operation and an
immutable typed control-result sidecar.  It intentionally does not copy
product verdicts, findings, acceptance matrices, or implementation details.
Those remain owned by the referenced control-result artifact.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from zf.runtime.sidecar_refs import (
    SidecarRefError,
    hydrate_sidecar_ref,
    sidecar_path,
    write_sidecar_text,
)


CALL_RESULT_ENVELOPE_SCHEMA = "call-result-envelope.v1"
CALL_RESULT_CANONICALIZATION = "call-result-canonical-json.v1"
EXECUTION_STATUSES = frozenset({"completed", "failed"})

_IDENTITY_KEYS = (
    "workflow_run_id",
    "goal_id",
    "parent_operation_id",
    "operation_id",
    "request_hash",
    "task_id",
    "attempt_id",
    "attempt_domain",
    "dispatch_id",
    "producer_stage_id",
    "producer_role",
    "fanout_id",
    "child_id",
    "plan_revision",
    "plan_synth_contract_ref",
    "plan_synth_contract_digest",
    "reviewed_plan_candidate_digest",
    "contract_authority_revision",
    "execution_owner",
    "workflow_request_id",
    "workflow_request_revision",
    "origin_binding_digest",
    "contract_revision",
    "task_map_generation",
    "feedback_revision",
    "workflow_generation",
    "request_revision",
    "generic_workflow_contract_digest",
    "workflow_intent",
    "workflow_template",
    "completion_profile",
    "result_semantics",
    "goal_claim_set_ref",
    "goal_claim_set_digest",
    "product_acceptance_required",
    "product_acceptance_spec_ref",
    "product_acceptance_spec_digest",
    "product_acceptance_report_ref",
    "product_acceptance_report_digest",
    "product_acceptance_verdict",
    "provider_qualification_required",
    "provider_qualification_status",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
    "run_contract_ref",
    "run_contract_digest",
    "base_commit",
    "task_ref",
    "contract_snapshot_ref",
    "contract_snapshot_digest",
    "target_snapshot_ref",
    "target_snapshot_digest",
    "target_commit",
)


class CallResultEnvelopeError(ValueError):
    """The envelope or one of its immutable refs is not trustworthy."""


def call_result_envelope_ref(value: object) -> str:
    """Return a canonical digest-addressed envelope ref or an empty string."""

    is_descriptor = isinstance(value, Mapping)
    descriptor = value if is_descriptor else {}
    ref = str(
        descriptor.get("ref") if is_descriptor else value or ""
    ).strip()
    path = PurePosixPath(ref)
    digest = str(
        descriptor.get("sha256") if is_descriptor else path.stem
    ).strip()
    if (
        not re.fullmatch(r"[0-9a-f]{64}", digest)
        or len(path.parts) != 4
        or path.parts[:3] != ("artifacts", "call-results", "envelopes")
        or path.suffix != ".json"
        or path.stem != digest
    ):
        return ""
    return ref


def canonical_json_bytes(payload: Any) -> bytes:
    """Return the versioned canonical JSON encoding used for digests."""

    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        + "\n"
    ).encode("utf-8")


def canonical_json_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def write_immutable_json_sidecar(
    state_dir: Path,
    payload: Mapping[str, Any],
    *,
    root: str,
    kind: str,
    schema_version: str,
    created_by: str,
    source_event_id: str = "",
    required: bool = True,
) -> dict[str, Any]:
    """Write a digest-addressed JSON sidecar without mutable overwrite.

    Repeating the same write returns the same descriptor.  If a file already
    exists at the digest address with different bytes, the operation fails
    closed instead of replacing evidence.
    """

    body = canonical_json_bytes(dict(payload))
    digest = hashlib.sha256(body).hexdigest()
    safe_root = "/".join(_safe_component(part) for part in root.split("/") if part)
    relative_ref = f"artifacts/{safe_root}/{digest}.json"
    target = sidecar_path(state_dir, relative_ref)
    if target.exists():
        if target.read_bytes() != body:
            raise CallResultEnvelopeError(
                f"immutable sidecar collision at {relative_ref}"
            )
    else:
        descriptor = write_sidecar_text(
            state_dir,
            relative_ref,
            body.decode("utf-8"),
            kind=kind,
            schema_version=schema_version,
            created_by=created_by,
            source_event_id=source_event_id,
            required=required,
            content_type="application/json",
        )
        if descriptor["sha256"] != digest:
            raise CallResultEnvelopeError("canonical sidecar digest drift")
        return descriptor
    return {
        "ref_schema_version": "sidecar-ref.v1",
        "kind": kind,
        "ref": relative_ref,
        "sha256": digest,
        "byte_count": len(body),
        "content_type": "application/json",
        "schema_version": schema_version,
        "encoding": "utf-8",
        "created_by": created_by,
        "source_event_id": source_event_id,
        "access_scope": {},
        "retention": {"class": "audit_required"},
        "required": required,
        "preview": "",
    }


def normalize_call_result_envelope(
    *,
    source_payload: Mapping[str, Any],
    control_result: Mapping[str, Any],
    workflow_run_id: str,
    operation_id: str,
    request_hash: str,
    source_event_id: str,
    source_event_type: str,
    actor: str = "",
    task_id: str = "",
    correlation_id: str = "",
    parent_operation_id: str = "",
    execution_status: str = "",
) -> dict[str, Any]:
    """Normalize a legacy role result into a thin deterministic envelope."""

    payload = dict(source_payload)
    identity_values = {
        "workflow_run_id": workflow_run_id
        or _text(payload, "workflow_run_id", "trace_id", "pdd_id")
        or correlation_id,
        "goal_id": _text(payload, "goal_id"),
        "parent_operation_id": parent_operation_id
        or _text(payload, "parent_operation_id"),
        "operation_id": operation_id or _text(payload, "operation_id"),
        "request_hash": request_hash or _text(payload, "request_hash"),
        "task_id": task_id or _text(payload, "task_id", "upstream_task_id"),
        "attempt_id": _text(payload, "attempt_id", "run_id", "dispatch_id"),
        "attempt_domain": _text(payload, "attempt_domain"),
        "dispatch_id": _text(payload, "dispatch_id", "run_id"),
        "producer_stage_id": _text(payload, "producer_stage_id", "stage_id", "stage_slot"),
        "producer_role": _text(payload, "producer_role", "role_instance", "role") or actor,
        "fanout_id": _text(payload, "fanout_id"),
        "child_id": _text(payload, "child_id", "child_run"),
        "plan_revision": _text(payload, "plan_revision"),
        "plan_synth_contract_ref": _text(payload, "plan_synth_contract_ref"),
        "plan_synth_contract_digest": _text(payload, "plan_synth_contract_digest"),
        "reviewed_plan_candidate_digest": _text(
            payload,
            "reviewed_plan_candidate_digest",
        ),
        "contract_authority_revision": _text(
            payload,
            "contract_authority_revision",
        ),
        "execution_owner": _text(payload, "execution_owner"),
        "workflow_request_id": _text(payload, "workflow_request_id"),
        "workflow_request_revision": _text(
            payload,
            "workflow_request_revision",
        ),
        "origin_binding_digest": _text(payload, "origin_binding_digest"),
        "contract_revision": _text(payload, "contract_revision"),
        "task_map_generation": _text(payload, "task_map_generation"),
        "feedback_revision": _text(
            payload,
            "feedback_revision",
            "rework_feedback_digest",
        ),
        "workflow_generation": _text(payload, "workflow_generation"),
        "request_revision": _text(payload, "request_revision"),
        "generic_workflow_contract_digest": _text(
            payload,
            "generic_workflow_contract_digest",
        ),
        "workflow_intent": _text(payload, "workflow_intent"),
        "workflow_template": _text(payload, "workflow_template"),
        "completion_profile": _text(payload, "completion_profile"),
        "result_semantics": _text(payload, "result_semantics"),
        "goal_claim_set_ref": _text(payload, "goal_claim_set_ref"),
        "goal_claim_set_digest": _text(
            payload,
            "goal_claim_set_digest",
        ),
        "product_acceptance_required": _text(
            payload,
            "product_acceptance_required",
        ),
        "product_acceptance_spec_ref": _text(
            payload,
            "product_acceptance_spec_ref",
        ),
        "product_acceptance_spec_digest": _text(
            payload,
            "product_acceptance_spec_digest",
        ),
        "product_acceptance_report_ref": _text(
            payload,
            "product_acceptance_report_ref",
        ),
        "product_acceptance_report_digest": _text(
            payload,
            "product_acceptance_report_digest",
        ),
        "product_acceptance_verdict": _text(
            payload,
            "product_acceptance_verdict",
        ),
        "provider_qualification_required": _text(
            payload,
            "provider_qualification_required",
        ),
        "provider_qualification_status": _text(
            payload,
            "provider_qualification_status",
        ),
        "plan_artifact_package_id": _text(
            payload,
            "plan_artifact_package_id",
        ),
        "plan_artifact_package_ref": _text(
            payload,
            "plan_artifact_package_ref",
        ),
        "plan_artifact_package_digest": _text(
            payload,
            "plan_artifact_package_digest",
        ),
        "run_contract_ref": _text(payload, "run_contract_ref"),
        "run_contract_digest": _text(payload, "run_contract_digest"),
        "base_commit": _text(payload, "base_commit", "base_git_head"),
        "task_ref": _text(payload, "task_ref"),
        "contract_snapshot_ref": _text(payload, "contract_snapshot_ref"),
        "contract_snapshot_digest": _text(payload, "contract_snapshot_digest"),
        "target_snapshot_ref": _text(payload, "target_snapshot_ref"),
        "target_snapshot_digest": _text(payload, "target_snapshot_digest"),
        "target_commit": _text(
            payload,
            "target_commit",
            "candidate_head_commit",
            "source_commit",
        ),
    }
    status = execution_status or _execution_status(source_event_type, payload)
    input_consumption = payload.get("input_consumption")
    if not isinstance(input_consumption, Mapping):
        input_consumption = {
            "policy_ref": _text(payload, "input_consumption_policy_ref"),
            "read_ledger_ref": _text(payload, "read_ledger_ref"),
            "read_ledger_digest": _text(payload, "read_ledger_digest"),
            "status": _text(payload, "input_consumption_status") or "not_required",
        }
    repair = payload.get("call_result_repair")
    if not isinstance(repair, Mapping):
        repair = {"rounds": 0, "repair_refs": []}
    provider_summary = payload.get("provider_operation_summary_ref")
    if not isinstance(provider_summary, Mapping):
        provider_summary = {}
    return {
        "schema_version": CALL_RESULT_ENVELOPE_SCHEMA,
        "canonicalization_version": CALL_RESULT_CANONICALIZATION,
        "identity": {
            key: str(identity_values.get(key) or "")
            for key in _IDENTITY_KEYS
        },
        "execution": {
            "status": status,
            "exit_code": _integer(payload.get("exit_code"), default=0 if status == "completed" else 1),
            "provider_session_id": _text(
                payload,
                "provider_session_id",
                "provider_session_ref",
                "session_ref",
            ),
            "started_at": _text(payload, "started_at"),
            "completed_at": _text(payload, "completed_at"),
        },
        "control_result": dict(control_result),
        "artifact_manifest_ref": _text(payload, "artifact_manifest_ref"),
        "artifact_manifest_digest": _text(payload, "artifact_manifest_digest"),
        "provider_operation_summary_ref": dict(provider_summary),
        "evidence_refs": _strings(payload.get("evidence_refs")),
        "input_consumption": dict(input_consumption),
        "repair": {
            "rounds": _integer(repair.get("rounds"), default=0),
            "repair_refs": _strings(repair.get("repair_refs")),
        },
        "source_event_ids": [source_event_id] if source_event_id else [],
    }


def validate_call_result_envelope(
    envelope: Mapping[str, Any],
    *,
    require_target_snapshot: bool = False,
    require_read_proof: bool = False,
    allow_incomplete_reads: bool = False,
) -> list[dict[str, str]]:
    """Return stable, machine-readable protocol issues."""

    issues: list[dict[str, str]] = []
    if str(envelope.get("schema_version") or "") != CALL_RESULT_ENVELOPE_SCHEMA:
        issues.append(_issue("schema_version", "unsupported_schema"))
    identity = envelope.get("identity")
    if not isinstance(identity, Mapping):
        return [*issues, _issue("identity", "missing_object")]
    for field in (
        "workflow_run_id",
        "operation_id",
        "request_hash",
        "attempt_id",
        "producer_role",
    ):
        if not str(identity.get(field) or "").strip():
            issues.append(_issue(f"identity.{field}", "missing_required"))
    if require_target_snapshot:
        for field in (
            "contract_snapshot_ref",
            "contract_snapshot_digest",
            "target_snapshot_ref",
            "target_snapshot_digest",
            "target_commit",
        ):
            if not str(identity.get(field) or "").strip():
                issues.append(_issue(f"identity.{field}", "missing_required"))
    execution = envelope.get("execution")
    if not isinstance(execution, Mapping):
        issues.append(_issue("execution", "missing_object"))
    elif str(execution.get("status") or "") not in EXECUTION_STATUSES:
        issues.append(_issue("execution.status", "enum_mismatch"))
    control = envelope.get("control_result")
    if not isinstance(control, Mapping):
        issues.append(_issue("control_result", "missing_object"))
    else:
        for field in ("schema_version", "ref", "sha256"):
            if not str(control.get(field) or "").strip():
                issues.append(_issue(f"control_result.{field}", "missing_required"))
    provider_summary = envelope.get("provider_operation_summary_ref")
    if provider_summary:
        if not isinstance(provider_summary, Mapping):
            issues.append(_issue(
                "provider_operation_summary_ref",
                "missing_object",
            ))
        else:
            for field in ("schema_version", "ref", "sha256"):
                if not str(provider_summary.get(field) or "").strip():
                    issues.append(_issue(
                        f"provider_operation_summary_ref.{field}",
                        "missing_required",
                    ))
    if require_read_proof:
        consumption = envelope.get("input_consumption")
        if not isinstance(consumption, Mapping):
            issues.append(_issue("input_consumption", "missing_object"))
        else:
            for field in ("read_ledger_ref", "read_ledger_digest"):
                if not str(consumption.get(field) or "").strip():
                    issues.append(_issue(f"input_consumption.{field}", "missing_required"))
            allowed_statuses = {"satisfied"}
            if (allow_incomplete_reads and isinstance(execution, Mapping)
                    and execution.get("status") == "failed" and isinstance(control, Mapping)
                    and control.get("schema_version") == "workflow-read-result.v1"):
                allowed_statuses.add("incomplete")
            if str(consumption.get("status") or "") not in allowed_statuses:
                issues.append(_issue("input_consumption.status", "not_satisfied"))
    return issues


def hydrate_call_result_envelope(
    state_dir: Path,
    descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        hydrated = hydrate_sidecar_ref(state_dir, dict(descriptor))
    except SidecarRefError as exc:
        raise CallResultEnvelopeError(str(exc)) from exc
    if not isinstance(hydrated.payload, dict):
        raise CallResultEnvelopeError("call-result envelope sidecar must contain JSON object")
    issues = validate_call_result_envelope(hydrated.payload)
    if issues:
        raise CallResultEnvelopeError(f"invalid call-result envelope: {issues}")
    return hydrated.payload


def envelope_identity_key(envelope: Mapping[str, Any]) -> tuple[str, str, str]:
    identity = envelope.get("identity") if isinstance(envelope.get("identity"), Mapping) else {}
    control = envelope.get("control_result") if isinstance(envelope.get("control_result"), Mapping) else {}
    return (
        str(identity.get("operation_id") or ""),
        str(identity.get("request_hash") or ""),
        str(control.get("sha256") or ""),
    )


def _execution_status(event_type: str, payload: Mapping[str, Any]) -> str:
    raw = _text(payload, "execution_status", "status").lower()
    if raw in {"failed", "failure", "error"} or event_type.endswith((".failed", ".blocked")):
        return "failed"
    return "completed"


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._") or "default"


def _text(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _strings(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value] if value else []
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _integer(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _issue(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code}


__all__ = [
    "CALL_RESULT_CANONICALIZATION",
    "CALL_RESULT_ENVELOPE_SCHEMA",
    "CallResultEnvelopeError",
    "call_result_envelope_ref",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "envelope_identity_key",
    "hydrate_call_result_envelope",
    "normalize_call_result_envelope",
    "validate_call_result_envelope",
    "write_immutable_json_sidecar",
]
