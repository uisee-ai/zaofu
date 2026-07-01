"""Tests for session state machine."""

from __future__ import annotations

import pytest
from zf.core.statemachine.session import SessionStateMachine
from zf.core.statemachine.task import InvalidTransition


class TestSessionStateMachine:
    def setup_method(self):
        self.sm = SessionStateMachine()

    def test_created_to_active(self):
        assert self.sm.transition("created", "active") == "active"

    def test_created_to_bootstrapping(self):
        assert self.sm.transition("created", "bootstrapping") == "bootstrapping"

    def test_active_to_degraded(self):
        assert self.sm.transition("active", "degraded") == "degraded"

    def test_degraded_to_active(self):
        assert self.sm.transition("degraded", "active") == "active"

    def test_active_to_shutdown(self):
        assert self.sm.transition("active", "shutdown_requested") == "shutdown_requested"

    def test_shutdown_to_stopped(self):
        assert self.sm.transition("shutdown_requested", "stopped") == "stopped"

    def test_stopped_is_terminal(self):
        assert not self.sm.can_transition("stopped", "active")

    def test_invalid_transition(self):
        with pytest.raises(InvalidTransition):
            self.sm.transition("stopped", "active")
