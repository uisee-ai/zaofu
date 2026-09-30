"""Product Acceptance report construction for the generic product TestKit."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from zf.runtime.product_acceptance import (
    validate_product_acceptance_report,
)


class AcceptanceReportError(ValueError):
    """Acceptance results are incomplete or inconsistent with their spec."""


def build_acceptance_report(
    *,
    spec: Mapping[str, Any],
    spec_ref: str,
    spec_digest: str,
    candidate_ref: str,
    target_commit: str,
    journey_results: Sequence[Mapping[str, Any]],
    provider_qualification_receipts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build and validate a Product Acceptance report from explicit results."""

    statuses = {str(item.get("status") or "") for item in journey_results}
    if not journey_results:
        raise AcceptanceReportError("journey_results must not be empty")
    if "failed" in statuses:
        verdict = "rejected"
    elif "blocked" in statuses:
        verdict = "blocked"
    elif statuses <= {"passed", "waived"}:
        verdict = "passed"
    else:
        raise AcceptanceReportError("journey result has an invalid status")
    report = {
        "schema_version": "product_acceptance_report.v1",
        "workflow_run_id": str(spec.get("workflow_run_id") or ""),
        "task_map_generation": str(spec.get("task_map_generation") or ""),
        "candidate_ref": candidate_ref,
        "target_commit": target_commit,
        "product_acceptance_spec_ref": spec_ref,
        "product_acceptance_spec_digest": spec_digest,
        "verdict": verdict,
        "journey_results": [dict(item) for item in journey_results],
        "provider_qualification_receipts": [dict(item) for item in provider_qualification_receipts],
    }
    try:
        validate_product_acceptance_report(
            report,
            spec=spec,
            expected={
                "workflow_run_id": spec.get("workflow_run_id"),
                "task_map_generation": spec.get("task_map_generation"),
                "candidate_ref": candidate_ref,
                "target_commit": target_commit,
            },
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise AcceptanceReportError(str(exc)) from exc
    return report

