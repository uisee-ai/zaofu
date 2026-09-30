"""Mechanical readiness oracle between product test evidence and delivery."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from .artifacts import ArtifactError, verify_artifact_index
from tests.testkit.evidence import write_json_atomic


def evaluate_delivery_readiness(
    result: Mapping[str, Any],
    *,
    evidence_root: Path,
    expected_run_id: str,
    expected_candidate_ref: str,
    expected_target_commit: str,
    acceptance_required: bool,
) -> dict[str, Any]:
    """Return a fail-closed decision; this never authorizes or performs merge."""

    reasons: list[str] = []

    if result.get("schema_version") != "verification-result.v1":
        reasons.append("verification_schema_invalid")
    if result.get("run_id") != expected_run_id:
        reasons.append("run_id_mismatch")
    if result.get("candidate_ref") != expected_candidate_ref:
        reasons.append("candidate_ref_mismatch")
    if result.get("target_commit") != expected_target_commit:
        reasons.append("target_commit_mismatch")
    if result.get("verdict") != "pass":
        reasons.append(f"verification_not_passed:{result.get('verdict') or 'missing'}")
    if result.get("missing_commands"):
        reasons.append("missing_commands")
    if (result.get("domain_coverage") or {}).get("missing"):
        reasons.append("domain_coverage_incomplete")
    if any(row.get("mandatory", True) and not row.get("passed") for row in result.get("commands") or []):
        reasons.append("mandatory_command_failed")
    if any(not row.get("passed") for row in result.get("cleanup") or []):
        reasons.append("cleanup_failed")

    artifact_index = result.get("artifact_index")
    indexed: dict[str, str] = {}
    artifact_index_valid = False
    try:
        verify_artifact_index(artifact_index, root=evidence_root)
    except (ArtifactError, TypeError) as exc:
        reasons.append(f"artifact_index_invalid:{exc}")
    else:
        artifact_index_valid = True
        indexed = {str(row["ref"]): str(row["digest"]) for row in artifact_index}

    acceptance = result.get("product_acceptance_report")
    if acceptance_required and not isinstance(acceptance, Mapping):
        reasons.append("product_acceptance_missing")
    elif acceptance_required:
        if acceptance.get("verdict") != "passed":
            reasons.append(f"product_acceptance_not_passed:{acceptance.get('verdict') or 'missing'}")
        if acceptance.get("workflow_run_id") != expected_run_id:
            reasons.append("acceptance_run_id_mismatch")
        if acceptance.get("candidate_ref") != expected_candidate_ref:
            reasons.append("acceptance_candidate_ref_mismatch")
        if acceptance.get("target_commit") != expected_target_commit:
            reasons.append("acceptance_target_commit_mismatch")
        for evidence in iter_acceptance_evidence(acceptance) if artifact_index_valid else ():
            ref = str(evidence.get("ref") or "")
            digest = str(evidence.get("sha256") or "")
            if not ref or not digest:
                reason = "acceptance_evidence_invalid"
            elif ref not in indexed:
                reason = f"acceptance_evidence_not_indexed:{ref}"
            elif indexed[ref] != digest:
                reason = f"acceptance_evidence_digest_mismatch:{ref}"
            else:
                continue
            if reason not in reasons:
                reasons.append(reason)

    return {
        "schema_version": "product-delivery-readiness.v1",
        "allowed": not reasons,
        "reasons": reasons,
        "run_id": expected_run_id,
        "candidate_ref": expected_candidate_ref,
        "target_commit": expected_target_commit,
        "acceptance_required": acceptance_required,
        # Readiness evidence cannot manufacture the separate operator or
        # ControlledAction authorization required to mutate a remote ref.
        "merge_authorized": False,
    }


def iter_acceptance_evidence(report: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """Yield every journey/assertion evidence reference in report order."""

    for journey in report.get("journey_results") or []:
        if not isinstance(journey, Mapping):
            continue
        for evidence in journey.get("evidence_refs") or []:
            if isinstance(evidence, Mapping):
                yield evidence
        for assertion in journey.get("assertion_results") or []:
            if not isinstance(assertion, Mapping):
                continue
            for evidence in assertion.get("evidence_refs") or []:
                if isinstance(evidence, Mapping):
                    yield evidence


def write_delivery_readiness(decision: Mapping[str, Any], path: Path) -> None:
    """Persist one deterministic readiness decision atomically."""

    write_json_atomic(path, dict(decision))

