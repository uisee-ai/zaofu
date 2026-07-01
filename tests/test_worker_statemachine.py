"""Tests for worker state machine."""

from __future__ import annotations

import pytest
from zf.core.statemachine.worker import WorkerStateMachine
from zf.core.statemachine.task import InvalidTransition


class TestWorkerStateMachine:
    def setup_method(self):
        self.sm = WorkerStateMachine()

    def test_idle_to_working(self):
        assert self.sm.transition("idle", "working") == "working"

    def test_working_to_refreshing(self):
        assert self.sm.transition("working", "refreshing") == "refreshing"

    def test_working_to_blocked(self):
        assert self.sm.transition("working", "blocked") == "blocked"

    def test_working_to_crashed(self):
        assert self.sm.transition("working", "crashed") == "crashed"

    def test_crashed_to_idle(self):
        assert self.sm.transition("crashed", "idle") == "idle"

    def test_stopping_is_terminal(self):
        assert not self.sm.can_transition("stopping", "idle")

    def test_invalid_transition(self):
        with pytest.raises(InvalidTransition):
            self.sm.transition("idle", "crashed")

    def test_valid_transitions_returns_dict(self):
        t = self.sm.valid_transitions()
        assert isinstance(t, dict)
        assert "idle" in t
