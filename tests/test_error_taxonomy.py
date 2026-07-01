"""LH-4.T1/T2/T6: FailureTaxonomy + CircuitBreaker + exponential backoff."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.errors.circuit_breaker import CircuitBreaker, CircuitState
from zf.core.errors.retry import exponential_backoff
from zf.core.errors.taxonomy import (
    FailureCategory, RetryPolicy, classify, policy_for,
)
from zf.core.events.model import ZfEvent


class TestTaxonomyCategories:
    def test_known_categories_present(self):
        for name in (
            "SYSTEM_IO", "SYSTEM_PROCESS", "SYSTEM_BACKEND",
            "BUSINESS_GATE", "BUSINESS_JUDGE", "BUSINESS_DISCRIMINATOR",
            "AGENT_STUCK", "AGENT_DRIFT", "AGENT_BUDGET",
        ):
            assert hasattr(FailureCategory, name), name

    def test_classify_stuck(self):
        cat = classify(ZfEvent(type="worker.stuck", actor="dev"))
        assert cat == FailureCategory.AGENT_STUCK

    def test_classify_drift(self):
        cat = classify(ZfEvent(type="worker.drift.detected", actor="zf-cli"))
        assert cat == FailureCategory.AGENT_DRIFT

    def test_classify_budget(self):
        cat = classify(ZfEvent(type="cost.budget.exceeded", actor="zf-cli"))
        assert cat == FailureCategory.AGENT_BUDGET

    def test_classify_review_rejected(self):
        cat = classify(ZfEvent(type="review.rejected", actor="review"))
        assert cat == FailureCategory.BUSINESS_GATE

    def test_classify_judge_failed(self):
        cat = classify(ZfEvent(type="judge.failed", actor="judge"))
        assert cat == FailureCategory.BUSINESS_JUDGE

    def test_classify_discriminator(self):
        cat = classify(ZfEvent(type="discriminator.failed", actor="zf-cli"))
        assert cat == FailureCategory.BUSINESS_DISCRIMINATOR

    def test_classify_unrelated_returns_none(self):
        assert classify(ZfEvent(type="loop.started", actor="zf-cli")) is None


class TestPolicyLookup:
    def test_policy_for_system_backend_has_retries(self):
        p = policy_for(FailureCategory.SYSTEM_BACKEND)
        assert p.max_retries >= 1
        assert p.backoff_seconds >= 0

    def test_policy_for_business_judge_shorter_retries(self):
        p = policy_for(FailureCategory.BUSINESS_JUDGE)
        assert p.max_retries <= 3


class TestExponentialBackoff:
    def test_monotonic_growth(self):
        a = exponential_backoff(1, base_seconds=1)
        b = exponential_backoff(2, base_seconds=1)
        c = exponential_backoff(3, base_seconds=1)
        assert a <= b <= c

    def test_floor_respected_on_zero_attempt(self):
        assert exponential_backoff(0, base_seconds=1) >= 0


class TestCircuitBreakerStateMachine:
    def test_starts_closed(self, tmp_path):
        br = CircuitBreaker(
            key=("dev", "t1"), max_failures=3, window_seconds=60,
            store_path=tmp_path / "circuits.json",
        )
        assert br.state() == CircuitState.CLOSED
        assert br.can_proceed()

    def test_trips_to_open_after_max_failures(self, tmp_path):
        br = CircuitBreaker(
            key=("dev", "t1"), max_failures=3, window_seconds=60,
            store_path=tmp_path / "circuits.json",
        )
        for _ in range(3):
            br.record_failure("rejected")
        assert br.state() == CircuitState.OPEN
        assert not br.can_proceed()

    def test_window_expiry_resets(self, tmp_path):
        # Use monotonic clock injection via _now attribute
        br = CircuitBreaker(
            key=("dev", "t1"), max_failures=2, window_seconds=10,
            store_path=tmp_path / "circuits.json",
        )
        br._now = lambda: 0.0
        br.record_failure("x")
        br.record_failure("y")
        assert br.state() == CircuitState.OPEN
        # Past the window, calls can proceed again (half-open).
        br._now = lambda: 100.0
        assert br.can_proceed()
        assert br.state() == CircuitState.HALF_OPEN

    def test_persistence_round_trip(self, tmp_path):
        path = tmp_path / "circuits.json"
        br1 = CircuitBreaker(
            key=("dev", "t1"), max_failures=2, window_seconds=60,
            store_path=path,
        )
        br1._now = lambda: 0.0
        br1.record_failure("a")
        br1.record_failure("b")
        # Reload
        br2 = CircuitBreaker(
            key=("dev", "t1"), max_failures=2, window_seconds=60,
            store_path=path,
        )
        br2._now = lambda: 1.0
        assert br2.state() == CircuitState.OPEN

    def test_separate_keys_independent(self, tmp_path):
        path = tmp_path / "circuits.json"
        a = CircuitBreaker(
            key=("dev", "t1"), max_failures=1, window_seconds=60,
            store_path=path,
        )
        b = CircuitBreaker(
            key=("dev", "t2"), max_failures=1, window_seconds=60,
            store_path=path,
        )
        a.record_failure("fail")
        assert a.state() == CircuitState.OPEN
        assert b.state() == CircuitState.CLOSED


class TestWireUp:
    def test_taxonomy_imported_from_reactor_or_dispatch(self):
        import pathlib
        roots = [
            pathlib.Path("src/zf/runtime/orchestrator_dispatch.py").read_text(),
            pathlib.Path("src/zf/runtime/orchestrator_reactor.py").read_text(),
        ]
        assert any(
            "FailureCategory" in t or "CircuitBreaker" in t for t in roots
        )
