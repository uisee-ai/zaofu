"""Host-neutral conservative auto-fill policy for Issue Draft enrichment."""

from __future__ import annotations

from .enrichment import Claim, DraftFieldState, Evidence


AUTO_FILL_FIELDS = frozenset({"actual_behavior", "environment", "raw_error"})


def can_auto_fill(
    field: DraftFieldState,
    *,
    claim: Claim,
    evidence: Evidence,
) -> bool:
    """Allow one fill only from verified observations and current evidence."""

    field.validate()
    claim.validate()
    evidence.validate()
    return (
        field.field_path in AUTO_FILL_FIELDS
        and field.auto_fill_eligible
        and claim.kind == "observed"
        and claim.verification_status == "verified"
        and evidence.integrity == "verified"
        and evidence.applicability == "current"
        and evidence.evidence_id in claim.dependency_refs
    )


__all__ = ["AUTO_FILL_FIELDS", "can_auto_fill"]
