"""Tests for R-TASK-STATE-AXIS-01: derive_phase from events.

phase is derived (not stored) from the most recent stage-progress
event for a task. Backlog tasks have no phase. Tasks dispatched
through dev → review → test → judge advance through phases as
events fire.
"""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.core.task.lifecycle import derive_phase
from zf.core.task.schema import Task


def _evt(type_: str, task_id: str) -> ZfEvent:
    return ZfEvent(type=type_, actor="t", task_id=task_id, payload={})


class TestDerivePhase:
    def test_backlog_task_returns_none(self):
        task = Task(id="T1", status="backlog")
        assert derive_phase(task, []) is None

    def test_no_events_returns_none(self):
        task = Task(id="T1", status="in_progress", assigned_to="dev-1")
        assert derive_phase(task, []) is None

    def test_dev_build_done_yields_build_done(self):
        task = Task(id="T1", status="in_progress", assigned_to="review")
        events = [_evt("dev.build.done", "T1")]
        assert derive_phase(task, events) == "build_done"

    def test_review_approved_yields_review_approved(self):
        task = Task(id="T1", status="in_progress", assigned_to="test")
        events = [
            _evt("dev.build.done", "T1"),
            _evt("review.approved", "T1"),
        ]
        assert derive_phase(task, events) == "review_approved"

    def test_test_passed_yields_test_passed(self):
        task = Task(id="T1", status="in_progress", assigned_to="judge")
        events = [
            _evt("dev.build.done", "T1"),
            _evt("review.approved", "T1"),
            _evt("test.passed", "T1"),
        ]
        assert derive_phase(task, events) == "test_passed"

    def test_judge_passed_yields_judge_passed(self):
        task = Task(id="T1", status="in_progress", assigned_to="judge")
        events = [
            _evt("dev.build.done", "T1"),
            _evt("review.approved", "T1"),
            _evt("test.passed", "T1"),
            _evt("judge.passed", "T1"),
        ]
        assert derive_phase(task, events) == "judge_passed"

    def test_other_task_events_ignored(self):
        task = Task(id="T1", status="in_progress")
        events = [
            _evt("dev.build.done", "T2"),  # different task
            _evt("review.approved", "OTHER"),
        ]
        assert derive_phase(task, events) is None

    def test_failure_phases_recorded(self):
        """Rework events should also surface as the latest phase."""
        task = Task(id="T1", status="in_progress")
        events = [
            _evt("dev.build.done", "T1"),
            _evt("review.rejected", "T1"),
        ]
        assert derive_phase(task, events) == "review_rejected"

    def test_test_failed_phase(self):
        task = Task(id="T1", status="in_progress")
        events = [
            _evt("dev.build.done", "T1"),
            _evt("review.approved", "T1"),
            _evt("test.failed", "T1"),
        ]
        assert derive_phase(task, events) == "test_failed"

    def test_arch_proposal_done(self):
        task = Task(id="T1", status="in_progress", assigned_to="review")
        events = [_evt("arch.proposal.done", "T1")]
        assert derive_phase(task, events) == "design_done"

    def test_unrelated_event_types_skipped(self):
        """Events that aren't stage-progress markers don't change phase."""
        task = Task(id="T1", status="in_progress")
        events = [
            _evt("dev.build.done", "T1"),
            _evt("agent.tool.use", "T1"),  # noise
            _evt("worker.state.changed", "T1"),  # noise
        ]
        assert derive_phase(task, events) == "build_done"

    def test_role_stages_argument_accepted_unused(self):
        """role_stages is forward-compat, currently ignored — verify
        it doesn't change behavior."""
        task = Task(id="T1", status="in_progress")
        events = [_evt("dev.build.done", "T1")]
        with_stages = derive_phase(task, events, role_stages=["build"])
        without_stages = derive_phase(task, events)
        assert with_stages == without_stages == "build_done"
