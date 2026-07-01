"""EVAL-WAVE-REVIEW-001 — wave_review aggregation tests."""

from __future__ import annotations

import pytest

from zf.runtime.wave_review import (
    REVIEWER_APPROVE,
    REVIEWER_REJECT,
    REVIEWER_SUSPEND,
    VALID_REVIEW_STRATEGIES,
    ReviewerOutcome,
    WaveReviewDecision,
    aggregate_review_wave,
)


def _o(reviewer: str, outcome: str) -> ReviewerOutcome:
    return ReviewerOutcome(reviewer_id=reviewer, outcome=outcome)


# ---------------------------------------------------------------------------
# Strategy enumeration
# ---------------------------------------------------------------------------


def test_valid_strategies_count() -> None:
    assert VALID_REVIEW_STRATEGIES == frozenset({
        "all_approve_or_one_rejects",
        "majority_approve",
        "any_approve_and_no_reject",
    })


def test_unknown_strategy_returns_pending() -> None:
    d = aggregate_review_wave(
        outcomes=[_o("a", REVIEWER_APPROVE)],
        strategy="totally_bogus",
        expected_reviewers=1,
    )
    assert d.verdict == "pending"
    assert "unknown" in d.reason


# ---------------------------------------------------------------------------
# all_approve_or_one_rejects (adversarial)
# ---------------------------------------------------------------------------


def test_all_approve_strategy_success() -> None:
    d = aggregate_review_wave(
        outcomes=[
            _o("a", REVIEWER_APPROVE),
            _o("b", REVIEWER_APPROVE),
            _o("c", REVIEWER_APPROVE),
        ],
        strategy="all_approve_or_one_rejects",
        expected_reviewers=3,
    )
    assert d.verdict == "success"
    assert d.approvals == 3


def test_all_approve_one_rejects_fails_fast() -> None:
    """Single reject → failure, even if quorum not met."""
    d = aggregate_review_wave(
        outcomes=[_o("a", REVIEWER_REJECT)],
        strategy="all_approve_or_one_rejects",
        expected_reviewers=3,
    )
    assert d.verdict == "failure"
    assert d.rejections == 1


def test_all_approve_partial_pending() -> None:
    """2/3 approve, 0 reject → pending."""
    d = aggregate_review_wave(
        outcomes=[
            _o("a", REVIEWER_APPROVE),
            _o("b", REVIEWER_APPROVE),
        ],
        strategy="all_approve_or_one_rejects",
        expected_reviewers=3,
    )
    assert d.verdict == "pending"


def test_all_approve_with_suspend_fails() -> None:
    """Adversarial mode requires all approve — a suspend = not approve."""
    d = aggregate_review_wave(
        outcomes=[
            _o("a", REVIEWER_APPROVE),
            _o("b", REVIEWER_APPROVE),
            _o("c", REVIEWER_SUSPEND),
        ],
        strategy="all_approve_or_one_rejects",
        expected_reviewers=3,
    )
    assert d.verdict == "failure"
    assert d.suspensions == 1


# ---------------------------------------------------------------------------
# majority_approve
# ---------------------------------------------------------------------------


def test_majority_2_of_3_passes() -> None:
    d = aggregate_review_wave(
        outcomes=[
            _o("a", REVIEWER_APPROVE),
            _o("b", REVIEWER_APPROVE),
            _o("c", REVIEWER_REJECT),
        ],
        strategy="majority_approve",
        expected_reviewers=3,
    )
    assert d.verdict == "success"


def test_majority_1_of_3_fails() -> None:
    d = aggregate_review_wave(
        outcomes=[
            _o("a", REVIEWER_APPROVE),
            _o("b", REVIEWER_REJECT),
            _o("c", REVIEWER_REJECT),
        ],
        strategy="majority_approve",
        expected_reviewers=3,
    )
    assert d.verdict == "failure"


def test_majority_1_of_2_tie_fails() -> None:
    """Tie (50/50) → failure (need >50%)."""
    d = aggregate_review_wave(
        outcomes=[
            _o("a", REVIEWER_APPROVE),
            _o("b", REVIEWER_REJECT),
        ],
        strategy="majority_approve",
        expected_reviewers=2,
    )
    assert d.verdict == "failure"


def test_majority_partial_pending() -> None:
    d = aggregate_review_wave(
        outcomes=[_o("a", REVIEWER_APPROVE)],
        strategy="majority_approve",
        expected_reviewers=3,
    )
    assert d.verdict == "pending"


# ---------------------------------------------------------------------------
# any_approve_and_no_reject (lenient)
# ---------------------------------------------------------------------------


def test_any_approve_passes_with_1_approve_no_reject() -> None:
    d = aggregate_review_wave(
        outcomes=[
            _o("a", REVIEWER_APPROVE),
            _o("b", REVIEWER_SUSPEND),
            _o("c", REVIEWER_SUSPEND),
        ],
        strategy="any_approve_and_no_reject",
        expected_reviewers=3,
    )
    assert d.verdict == "success"


def test_any_approve_fails_when_anyone_rejects() -> None:
    d = aggregate_review_wave(
        outcomes=[
            _o("a", REVIEWER_APPROVE),
            _o("b", REVIEWER_APPROVE),
            _o("c", REVIEWER_REJECT),
        ],
        strategy="any_approve_and_no_reject",
        expected_reviewers=3,
    )
    assert d.verdict == "failure"


def test_any_approve_all_suspend_fails() -> None:
    """No approvals + no rejections (all abstain) → failure."""
    d = aggregate_review_wave(
        outcomes=[
            _o("a", REVIEWER_SUSPEND),
            _o("b", REVIEWER_SUSPEND),
            _o("c", REVIEWER_SUSPEND),
        ],
        strategy="any_approve_and_no_reject",
        expected_reviewers=3,
    )
    assert d.verdict == "failure"
    assert "no approvals" in d.reason


def test_any_approve_partial_pending() -> None:
    d = aggregate_review_wave(
        outcomes=[_o("a", REVIEWER_APPROVE)],
        strategy="any_approve_and_no_reject",
        expected_reviewers=3,
    )
    assert d.verdict == "pending"


# ---------------------------------------------------------------------------
# Custom quorum
# ---------------------------------------------------------------------------


def test_quorum_overrides_expected_reviewers() -> None:
    """quorum=2 + 3 expected → 2 responses enough for verdict."""
    d = aggregate_review_wave(
        outcomes=[
            _o("a", REVIEWER_APPROVE),
            _o("b", REVIEWER_APPROVE),
        ],
        strategy="majority_approve",
        expected_reviewers=3,
        quorum=2,
    )
    # 2/3 = 67% which is majority → success
    assert d.verdict == "success"


def test_quorum_zero_means_wait_for_all() -> None:
    """quorum=0 defaults to expected_reviewers."""
    d = aggregate_review_wave(
        outcomes=[
            _o("a", REVIEWER_APPROVE),
            _o("b", REVIEWER_APPROVE),
        ],
        strategy="majority_approve",
        expected_reviewers=3,
        quorum=0,
    )
    assert d.verdict == "pending"


# ---------------------------------------------------------------------------
# WaveReviewDecision data shape
# ---------------------------------------------------------------------------


def test_decision_is_frozen() -> None:
    d = WaveReviewDecision(
        strategy="majority_approve",
        expected_reviewers=3,
        responded=2,
        approvals=2,
        rejections=0,
        suspensions=0,
        verdict="pending",
    )
    with pytest.raises((AttributeError, TypeError)):
        d.verdict = "success"  # type: ignore[misc]


def test_decision_includes_counts() -> None:
    d = aggregate_review_wave(
        outcomes=[
            _o("a", REVIEWER_APPROVE),
            _o("b", REVIEWER_REJECT),
            _o("c", REVIEWER_SUSPEND),
        ],
        strategy="majority_approve",
        expected_reviewers=3,
    )
    assert d.approvals == 1
    assert d.rejections == 1
    assert d.suspensions == 1
    assert d.responded == 3


# ---------------------------------------------------------------------------
# Config schema integration
# ---------------------------------------------------------------------------


def test_fanout_aggregate_config_has_review_strategy_field() -> None:
    """schema.FanoutAggregateConfig has review_strategy default ''."""
    from zf.core.config.schema import FanoutAggregateConfig

    cfg = FanoutAggregateConfig()
    assert hasattr(cfg, "review_strategy")
    assert cfg.review_strategy == ""
    assert hasattr(cfg, "pending_event")
    assert hasattr(cfg, "quorum")
    assert cfg.quorum == 0


def test_reviewer_outcome_is_frozen() -> None:
    o = ReviewerOutcome(reviewer_id="a", outcome=REVIEWER_APPROVE)
    with pytest.raises((AttributeError, TypeError)):
        o.outcome = REVIEWER_REJECT  # type: ignore[misc]
