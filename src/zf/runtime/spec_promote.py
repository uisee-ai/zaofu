"""ZF-LH-SPEC-PROMOTE-001 — judge.passed → spec.promote.{completed,skipped} (doc 26 §4.1).

After ``judge.passed``, the kernel must record whether the verified
behavior was promoted into the canonical spec / ADR or skipped (with
reason). This closes the "judge approved but spec is stale" loop —
without promotion, future tasks lose the deferred learning.

The module supplies the pure decision function. Reactor integration
+ event registration land here so the contract is uniformly callable
from orchestrator dispatch / Web UI / handoff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


SPEC_PROMOTE_COMPLETED = "spec.promote.completed"
SPEC_PROMOTE_SKIPPED = "spec.promote.skipped"


@dataclass(frozen=True)
class SpecPromoteDecision:
    """Result of evaluating whether a judge-passed task should
    promote into the spec."""

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
    """Decide whether to emit ``spec.promote.completed`` or
    ``.skipped``.

    Skip criteria (any one → skip):
    - operator_override_skip True → skip with operator reason
    - is_bug_fix_only True → skip ("bug fix, no spec change")
    - has_acceptance_evidence False → skip ("no acceptance evidence")
    - spec_ref empty → skip ("no spec_ref to promote into")

    Otherwise → promote.
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
        event_type=SPEC_PROMOTE_COMPLETED,
        reason="promoted",
        spec_ref=spec_ref,
        task_id=task_id,
    )
