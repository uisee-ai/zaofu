"""Tests for loop state machine."""

from __future__ import annotations

import pytest
from zf.core.statemachine.loop import LoopStateMachine
from zf.core.statemachine.task import InvalidTransition


class TestLoopStateMachine:
    def setup_method(self):
        self.sm = LoopStateMachine()

    def test_starting_to_running(self):
        assert self.sm.transition("starting", "running") == "running"

    def test_running_to_waiting(self):
        assert self.sm.transition("running", "waiting") == "waiting"

    def test_waiting_to_running(self):
        assert self.sm.transition("waiting", "running") == "running"

    def test_running_to_paused(self):
        assert self.sm.transition("running", "paused") == "paused"

    def test_paused_to_running(self):
        assert self.sm.transition("paused", "running") == "running"

    def test_running_to_completed(self):
        assert self.sm.transition("running", "completed") == "completed"

    def test_completed_is_terminal(self):
        assert not self.sm.can_transition("completed", "running")

    def test_failed_to_recovering(self):
        assert self.sm.transition("failed", "recovering") == "recovering"

    def test_terminated_is_terminal(self):
        assert not self.sm.can_transition("terminated", "running")

    def test_invalid_transition(self):
        with pytest.raises(InvalidTransition):
            self.sm.transition("completed", "running")
