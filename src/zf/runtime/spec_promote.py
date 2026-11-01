"""Audit specification promotion separately from accepted implementation.

The judge callback does not update specifications or receive an admitted
specification-update receipt. Record that limitation explicitly; a spec ref
and accepted implementation do not prove a specification update. The completed
event name remains registered for historical replay, not automatic emission.
"""

from __future__ import annotations

from dataclasses import dataclass


SPEC_PROMOTE_COMPLETED = "spec.promote.completed"
SPEC_PROMOTE_SKIPPED = "spec.promote.skipped"


@dataclass(frozen=True)
class SpecPromoteDecision:
    """Audit decision after task acceptance; not an instruction to edit intent."""

    event_type: str
    reason: str
    spec_ref: str = ""
    task_id: str = ""

    @property
    def is_promotion(self) -> bool:
        return self.event_type == SPEC_PROMOTE_COMPLETED


def decide_promotion(
    *,
    task_id: str,
    spec_ref: str = "",
    has_acceptance_evidence: bool = True,
    is_bug_fix_only: bool = False,
    operator_override_skip: bool = False,
    operator_override_reason: str = "",
) -> SpecPromoteDecision:
    """Explain why the judge callback cannot claim specification promotion.

    Skip criteria (any one → skip):
    - operator_override_skip True → skip with operator reason
    - is_bug_fix_only True → skip ("bug fix, no spec change")
    - has_acceptance_evidence False → skip ("no acceptance evidence")
    - spec_ref empty → skip ("no spec_ref to promote into")

    Otherwise → skip: no verified specification-update receipt exists on this
    path. Keep legacy arguments/reasons, but never infer promotion from them.
    """
    if operator_override_skip:
        return SpecPromoteDecision(
            event_type=SPEC_PROMOTE_SKIPPED,
            reason=operator_override_reason or "operator_skip",
            spec_ref=spec_ref,
            task_id=task_id,
        )
    if is_bug_fix_only:
        return SpecPromoteDecision(
            event_type=SPEC_PROMOTE_SKIPPED,
            reason="bug_fix_only",
            spec_ref=spec_ref,
            task_id=task_id,
        )
    if not has_acceptance_evidence:
        return SpecPromoteDecision(
            event_type=SPEC_PROMOTE_SKIPPED,
            reason="no_acceptance_evidence",
            spec_ref=spec_ref,
            task_id=task_id,
        )
    if not spec_ref:
        return SpecPromoteDecision(
            event_type=SPEC_PROMOTE_SKIPPED,
            reason="no_spec_ref",
            spec_ref=spec_ref,
            task_id=task_id,
        )
    return SpecPromoteDecision(
        event_type=SPEC_PROMOTE_SKIPPED,
        reason="promotion_not_verified",
        spec_ref=spec_ref,
        task_id=task_id,
    )
