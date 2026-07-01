"""EVAL-WAVE-REVIEW-001 — wave_review aggregation (doc 43 §2.6).

When a stage is configured with ``aggregate.review_strategy``, multiple
reviewers run in parallel against the same dispatched artefact and an
aggregator decides the outcome based on the strategy:

- ``all_approve_or_one_rejects`` — adversarial; ANY reject fails
- ``majority_approve`` — > 50% approve passes
- ``any_approve_and_no_reject`` — lenient; ≥ 1 approve and no reject

Pure function. Caller (fanout aggregator / orchestrator reactor) feeds
collected per-reviewer outcomes; returns the aggregate decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# Per-reviewer outcomes (terminology aligned with event types).
REVIEWER_APPROVE: str = "approve"     # review.approved
REVIEWER_REJECT: str = "reject"       # review.rejected
REVIEWER_SUSPEND: str = "suspend"     # review.suspended (LH-3, abstain)


VALID_REVIEW_STRATEGIES: frozenset[str] = frozenset({
    "all_approve_or_one_rejects",
    "majority_approve",
    "any_approve_and_no_reject",
})


@dataclass(frozen=True)
class ReviewerOutcome:
    """One reviewer's vote in a wave."""

    reviewer_id: str
    outcome: str            # approve | reject | suspend
    event_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class WaveReviewDecision:
    """Aggregated wave review verdict."""

    strategy: str
    expected_reviewers: int     # quorum (or roles count)
    responded: int
    approvals: int
    rejections: int
    suspensions: int
    verdict: str                # "success" | "failure" | "pending"
    reason: str = ""


def aggregate_review_wave(
    *,
    outcomes: Iterable[ReviewerOutcome],
    strategy: str,
    expected_reviewers: int,
    quorum: int = 0,
) -> WaveReviewDecision:
    """Aggregate a list of per-reviewer outcomes into a verdict.

    Args:
        outcomes: list of ReviewerOutcome (approve/reject/suspend)
        strategy: one of VALID_REVIEW_STRATEGIES; unknown → pending
        expected_reviewers: total reviewers dispatched to the wave
        quorum: minimum responses before aggregating; 0 →
            expected_reviewers (wait for all). Pending until met.

    Returns:
        WaveReviewDecision with verdict in {success, failure, pending}.
    """
    outcomes_list = list(outcomes)
    approvals = sum(1 for o in outcomes_list if o.outcome == REVIEWER_APPROVE)
    rejections = sum(1 for o in outcomes_list if o.outcome == REVIEWER_REJECT)
    suspensions = sum(
        1 for o in outcomes_list if o.outcome == REVIEWER_SUSPEND
    )
    responded = len(outcomes_list)
    effective_quorum = quorum or expected_reviewers

    # Quorum gate: regardless of strategy, if not enough reviewers
    # responded → pending. (Exception: any reject can fail-fast under
    # all_approve_or_one_rejects.)
    if strategy not in VALID_REVIEW_STRATEGIES:
        return WaveReviewDecision(
            strategy=strategy, expected_reviewers=expected_reviewers,
            responded=responded, approvals=approvals,
            rejections=rejections, suspensions=suspensions,
            verdict="pending",
            reason=f"unknown review_strategy {strategy!r}",
        )

    if strategy == "all_approve_or_one_rejects":
        if rejections > 0:
            return WaveReviewDecision(
                strategy=strategy, expected_reviewers=expected_reviewers,
                responded=responded, approvals=approvals,
                rejections=rejections, suspensions=suspensions,
                verdict="failure",
                reason=f"{rejections} reviewer(s) rejected",
            )
        if responded < effective_quorum:
            return WaveReviewDecision(
                strategy=strategy, expected_reviewers=expected_reviewers,
                responded=responded, approvals=approvals,
                rejections=rejections, suspensions=suspensions,
                verdict="pending",
                reason=(
                    f"waiting for {effective_quorum - responded} "
                    f"more reviewer(s)"
                ),
            )
        if approvals == expected_reviewers:
            return WaveReviewDecision(
                strategy=strategy, expected_reviewers=expected_reviewers,
                responded=responded, approvals=approvals,
                rejections=rejections, suspensions=suspensions,
                verdict="success",
                reason="all reviewers approved",
            )
        # Edge: quorum met but some suspended (abstain) — adversarial
        # mode requires ALL approve, so this is failure.
        return WaveReviewDecision(
            strategy=strategy, expected_reviewers=expected_reviewers,
            responded=responded, approvals=approvals,
            rejections=rejections, suspensions=suspensions,
            verdict="failure",
            reason=(
                f"only {approvals}/{expected_reviewers} approved "
                f"({suspensions} suspended); adversarial mode requires all"
            ),
        )

    if strategy == "majority_approve":
        if responded < effective_quorum:
            return WaveReviewDecision(
                strategy=strategy, expected_reviewers=expected_reviewers,
                responded=responded, approvals=approvals,
                rejections=rejections, suspensions=suspensions,
                verdict="pending",
                reason=(
                    f"waiting for {effective_quorum - responded} "
                    f"more reviewer(s)"
                ),
            )
        if approvals * 2 > expected_reviewers:
            return WaveReviewDecision(
                strategy=strategy, expected_reviewers=expected_reviewers,
                responded=responded, approvals=approvals,
                rejections=rejections, suspensions=suspensions,
                verdict="success",
                reason=f"majority ({approvals}/{expected_reviewers}) approved",
            )
        return WaveReviewDecision(
            strategy=strategy, expected_reviewers=expected_reviewers,
            responded=responded, approvals=approvals,
            rejections=rejections, suspensions=suspensions,
            verdict="failure",
            reason=f"only {approvals}/{expected_reviewers} approved (no majority)",
        )

    # any_approve_and_no_reject
    if responded < effective_quorum:
        return WaveReviewDecision(
            strategy=strategy, expected_reviewers=expected_reviewers,
            responded=responded, approvals=approvals,
            rejections=rejections, suspensions=suspensions,
            verdict="pending",
            reason=(
                f"waiting for {effective_quorum - responded} "
                f"more reviewer(s)"
            ),
        )
    if rejections > 0:
        return WaveReviewDecision(
            strategy=strategy, expected_reviewers=expected_reviewers,
            responded=responded, approvals=approvals,
            rejections=rejections, suspensions=suspensions,
            verdict="failure",
            reason=f"{rejections} reviewer(s) rejected",
        )
    if approvals >= 1:
        return WaveReviewDecision(
            strategy=strategy, expected_reviewers=expected_reviewers,
            responded=responded, approvals=approvals,
            rejections=rejections, suspensions=suspensions,
            verdict="success",
            reason=f"{approvals} approval(s), no rejections",
        )
    return WaveReviewDecision(
        strategy=strategy, expected_reviewers=expected_reviewers,
        responded=responded, approvals=approvals,
        rejections=rejections, suspensions=suspensions,
        verdict="failure",
        reason="no approvals and no rejections (all suspended)",
    )
