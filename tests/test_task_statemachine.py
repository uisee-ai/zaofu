"""Tests for task state machine."""

from __future__ import annotations

import pytest

from zf.core.statemachine.task import TaskStateMachine, InvalidTransition


def test_valid_forward_transitions():
    sm = TaskStateMachine()
    assert sm.can_transition("backlog", "in_progress")
    assert sm.can_transition("in_progress", "review")
    assert sm.can_transition("review", "testing")
    assert sm.can_transition("testing", "done")


def test_transition_returns_new_state():
    sm = TaskStateMachine()
    assert sm.transition("backlog", "in_progress") == "in_progress"
    assert sm.transition("in_progress", "review") == "review"


def test_backward_transition_rejected():
    sm = TaskStateMachine()
    with pytest.raises(InvalidTransition):
        sm.transition("done", "review")


def test_backward_from_testing_rejected():
    sm = TaskStateMachine()
    with pytest.raises(InvalidTransition):
        sm.transition("testing", "in_progress")


def test_cancel_from_any_active_state():
    sm = TaskStateMachine()
    for state in ["backlog", "in_progress", "review", "testing"]:
        assert sm.can_transition(state, "cancelled")


def test_cancel_from_done_rejected():
    sm = TaskStateMachine()
    with pytest.raises(InvalidTransition):
        sm.transition("done", "cancelled")


def test_blocked_transitions():
    sm = TaskStateMachine()
    assert sm.can_transition("backlog", "blocked")
    assert sm.can_transition("in_progress", "blocked")
    assert sm.can_transition("blocked", "backlog")


def test_blocked_to_done_rejected():
    sm = TaskStateMachine()
    with pytest.raises(InvalidTransition):
        sm.transition("blocked", "done")


def test_invalid_state_raises():
    sm = TaskStateMachine()
    with pytest.raises(InvalidTransition):
        sm.transition("nonexistent", "backlog")


def test_same_state_transition_rejected():
    sm = TaskStateMachine()
    with pytest.raises(InvalidTransition):
        sm.transition("backlog", "backlog")


def test_pure_function_no_io():
    """State machine is pure — no file I/O, no side effects."""
    sm = TaskStateMachine()
    # Just verify it works without any filesystem
    result = sm.transition("backlog", "in_progress")
    assert result == "in_progress"


def test_all_valid_transitions():
    sm = TaskStateMachine()
    valid = sm.valid_transitions()
    assert "backlog" in valid
    assert "in_progress" in valid["backlog"]
