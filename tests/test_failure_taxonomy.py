"""EVAL-FAILURE-TAXONOMY-001 — 7-category retryable taxonomy."""

from __future__ import annotations

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.rework_triage import (
    CONTENT_CLASSIFICATIONS,
    INFRA_CLASSIFICATIONS,
    REWORK_RETRY_CLASSIFICATIONS,
    ReworkTriageResult,
    TERMINAL_CLASSIFICATIONS,
    derive_is_terminal,
    derive_retryable,
    derive_taxonomy_bucket,
)


# ---------------------------------------------------------------------------
# Taxonomy buckets — set membership
# ---------------------------------------------------------------------------


def test_infra_classifications_count() -> None:
    """Doc 43 §2.2 lists 4 infra-class classifications."""
    assert INFRA_CLASSIFICATIONS == frozenset({
        "transport_failed",
        "worker_stuck",
        "provider_timeout",
        "runtime_offline",
    })


def test_terminal_classifications_count() -> None:
    assert TERMINAL_CLASSIFICATIONS == frozenset({
        "iteration_limit",
        "agent_fallback_message",
        "api_invalid_request",
    })


def test_content_classifications_includes_legacy() -> None:
    """Legacy product_issue / design_issue / yaml_routing must stay in
    the content bucket for backward compat."""
    assert "product_issue" in CONTENT_CLASSIFICATIONS
    assert "design_issue" in CONTENT_CLASSIFICATIONS
    assert "yaml_routing" in CONTENT_CLASSIFICATIONS


def test_buckets_are_disjoint() -> None:
    """No classification can belong to two buckets at once."""
    assert INFRA_CLASSIFICATIONS.isdisjoint(TERMINAL_CLASSIFICATIONS)
    assert INFRA_CLASSIFICATIONS.isdisjoint(CONTENT_CLASSIFICATIONS)
    assert TERMINAL_CLASSIFICATIONS.isdisjoint(CONTENT_CLASSIFICATIONS)


def test_legacy_retry_classifications_unchanged() -> None:
    """REWORK_RETRY_CLASSIFICATIONS retains historical semantic (used by
    dispatch retry-cap path), independent of new taxonomy buckets.
    phase_gate_violation joined in d9c70ec (#U: retry the original
    vertical after arch re-plans, advancing the 4-level cap counter)."""
    assert REWORK_RETRY_CLASSIFICATIONS == frozenset({
        "product_issue", "design_issue", "yaml_routing",
        "phase_gate_violation",
    })


# ---------------------------------------------------------------------------
# Derived helpers
# ---------------------------------------------------------------------------


def test_derive_retryable_infra_true() -> None:
    assert derive_retryable("transport_failed") is True
    assert derive_retryable("worker_stuck") is True
    assert derive_retryable("provider_timeout") is True


def test_derive_retryable_content_false() -> None:
    assert derive_retryable("product_issue") is False
    assert derive_retryable("review_rejected_content") is False
    assert derive_retryable("scope_violation") is False


def test_derive_retryable_terminal_false() -> None:
    assert derive_retryable("iteration_limit") is False
    assert derive_retryable("api_invalid_request") is False


def test_derive_retryable_unknown_false() -> None:
    """Unknown classifications default to non-retryable (safer)."""
    assert derive_retryable("mystery_classification") is False
    assert derive_retryable("") is False


def test_derive_is_terminal_only_terminal() -> None:
    assert derive_is_terminal("iteration_limit") is True
    assert derive_is_terminal("transport_failed") is False
    assert derive_is_terminal("product_issue") is False


def test_derive_taxonomy_bucket() -> None:
    assert derive_taxonomy_bucket("transport_failed") == "infra"
    assert derive_taxonomy_bucket("iteration_limit") == "terminal"
    assert derive_taxonomy_bucket("product_issue") == "content"
    assert derive_taxonomy_bucket("mystery") == "unknown"


# ---------------------------------------------------------------------------
# ReworkTriageResult auto-derivation in __post_init__
# ---------------------------------------------------------------------------


def test_result_auto_derives_retryable_from_infra() -> None:
    r = ReworkTriageResult(
        classification="transport_failed",
        gate_rule="r1",
        suspected_owner="kernel",
        recommended_action="retry",
        should_increment_retry=False,
    )
    assert r.retryable is True
    assert r.is_terminal is False
    assert r.taxonomy_bucket == "infra"


def test_result_auto_derives_terminal_from_iteration_limit() -> None:
    r = ReworkTriageResult(
        classification="iteration_limit",
        gate_rule="r1",
        suspected_owner="agent",
        recommended_action="escalate",
        should_increment_retry=False,
    )
    assert r.is_terminal is True
    assert r.retryable is False
    assert r.taxonomy_bucket == "terminal"


def test_result_auto_derives_content_from_product_issue() -> None:
    r = ReworkTriageResult(
        classification="product_issue",
        gate_rule="r1",
        suspected_owner="dev",
        recommended_action="rework",
        should_increment_retry=True,
    )
    assert r.retryable is False
    assert r.is_terminal is False
    assert r.taxonomy_bucket == "content"


def test_result_explicit_overrides_derivation() -> None:
    """Caller can override the derived fields when they have more
    context than the classification name reveals."""
    r = ReworkTriageResult(
        classification="agent_error",
        gate_rule="r1",
        suspected_owner="dev",
        recommended_action="rework",
        should_increment_retry=True,
        retryable=True,  # Explicit override
        taxonomy_bucket="infra",  # Explicit override
    )
    assert r.retryable is True
    assert r.taxonomy_bucket == "infra"


def test_payload_includes_taxonomy_fields() -> None:
    r = ReworkTriageResult(
        classification="worker_stuck",
        gate_rule="r1",
        suspected_owner="kernel",
        recommended_action="retry",
        should_increment_retry=False,
    )
    event = ZfEvent(type="worker.stuck", id="evt-1", actor="zf-cli")
    payload = r.to_payload(event)
    assert payload["retryable"] is True
    assert payload["is_terminal"] is False
    assert payload["taxonomy_bucket"] == "infra"


# ---------------------------------------------------------------------------
# Backward compat — existing classifications keep their semantic
# ---------------------------------------------------------------------------


def test_existing_product_issue_payload_keys_present() -> None:
    """Pre-EVAL-FAILURE-TAXONOMY payload keys must still appear."""
    r = ReworkTriageResult(
        classification="product_issue",
        gate_rule="rule-x",
        suspected_owner="dev",
        recommended_action="rework",
        should_increment_retry=True,
        notes="legacy note",
    )
    event = ZfEvent(type="review.rejected", id="evt-2", actor="zf-cli")
    payload = r.to_payload(event)
    # 9 legacy keys
    for key in (
        "task_id", "failed_event_id", "failed_event_type",
        "classification", "gate_rule", "suspected_owner",
        "recommended_action", "should_increment_retry", "notes",
    ):
        assert key in payload
