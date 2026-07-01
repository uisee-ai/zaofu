"""Tests for worker refresh policy."""

from __future__ import annotations

from zf.runtime.refresh import RefreshPolicy


class TestRefreshPolicy:
    def test_no_refresh_when_fresh(self):
        policy = RefreshPolicy()
        assert policy.evaluate(turn_count=0) is None

    def test_refresh_on_turns(self):
        policy = RefreshPolicy(max_turns=5)
        trigger = policy.evaluate(turn_count=5)
        assert trigger is not None
        assert trigger.reason == "turns_elapsed"

    def test_refresh_on_task_complete(self):
        policy = RefreshPolicy()
        trigger = policy.evaluate(task_just_completed=True)
        assert trigger is not None
        assert trigger.reason == "task_complete"

    def test_refresh_on_drift(self):
        policy = RefreshPolicy()
        trigger = policy.evaluate(drift_detected=True)
        assert trigger is not None
        assert trigger.reason == "drift"

    def test_refresh_on_context_pressure(self):
        policy = RefreshPolicy(context_pressure_threshold=0.7)
        trigger = policy.evaluate(context_pressure=0.8)
        assert trigger is not None
        assert trigger.reason == "context_pressure"

    def test_refresh_on_failures(self):
        policy = RefreshPolicy(max_failures=3)
        trigger = policy.evaluate(consecutive_failures=3)
        assert trigger is not None
        assert trigger.reason == "failures"

    def test_priority_order(self):
        """turns_elapsed checked before task_complete."""
        policy = RefreshPolicy(max_turns=5)
        trigger = policy.evaluate(turn_count=5, task_just_completed=True)
        assert trigger.reason == "turns_elapsed"
