"""Tests for R-TASK-STATE-AXIS-01: WipEnforcer with events-derived
in-flight count.

Same fix as B-REASSIGN-DISPATCH-01 applied to dispatch.py's C3 branch
is now generalized at WipEnforcer.can_accept. Tests cover:
  - legacy path (latest_dispatched=None or {}) preserves prior behavior
  - new path (non-empty dict) ignores merely-assigned-but-not-dispatched
    peers
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.task.wip import WipEnforcer


@pytest.fixture
def store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "kanban.json")


def _add(store: TaskStore, *, id_: str, assigned: str, status: str) -> None:
    store.add(Task(id=id_, title=id_, status=status, assigned_to=assigned))


class TestLegacyBehaviorPreserved:
    def test_no_arg_returns_legacy_count(self, store):
        # Two tasks both at dev-1, in_progress. WIP=1 → not accept.
        _add(store, id_="T1", assigned="dev-1", status="in_progress")
        _add(store, id_="T2", assigned="dev-1", status="in_progress")
        wip = WipEnforcer(limit=1)
        assert wip.can_accept("dev-1", store) is False

    def test_empty_dict_treated_as_legacy(self, store):
        """Empty latest_dispatched (no events observed) must fall back
        to assigned_to count — otherwise fixtures that bypass events
        would always allow dispatch."""
        _add(store, id_="T1", assigned="dev-1", status="in_progress")
        wip = WipEnforcer(limit=1)
        assert wip.can_accept("dev-1", store, {}) is False

    def test_idle_worker_accepts_under_legacy(self, store):
        wip = WipEnforcer(limit=1)
        assert wip.can_accept("dev-1", store) is True


class TestEventDrivenInFlight:
    def test_assigned_but_not_dispatched_does_not_count(self, store):
        """The B-REASSIGN-DISPATCH-01 scenario: 5 tasks all reassigned
        to review (assigned_to=review, status=in_progress) but events
        show their latest_dispatched is still dev. None of them count
        as occupying review's slot, so review accepts."""
        for i in range(5):
            _add(store, id_=f"T{i}", assigned="review", status="in_progress")
        latest = {f"T{i}": "dev-1" for i in range(5)}
        wip = WipEnforcer(limit=1)
        assert wip.can_accept("review", store, latest) is True

    def test_actually_dispatched_does_count(self, store):
        _add(store, id_="T1", assigned="review", status="in_progress")
        latest = {"T1": "review"}
        wip = WipEnforcer(limit=1)
        assert wip.can_accept("review", store, latest) is False

    def test_terminal_task_excluded(self, store):
        """Done/cancelled tasks don't occupy slots even if events say
        they were last dispatched here."""
        _add(store, id_="T1", assigned="review", status="done")
        latest = {"T1": "review"}
        wip = WipEnforcer(limit=1)
        assert wip.can_accept("review", store, latest) is True

    def test_other_worker_dispatch_does_not_count(self, store):
        _add(store, id_="T1", assigned="review", status="in_progress")
        latest = {"T1": "review"}
        wip = WipEnforcer(limit=1)
        # Asking for dev-1's WIP — T1 is dispatched to review, not dev-1.
        assert wip.can_accept("dev-1", store, latest) is True

    def test_limit_2_with_one_dispatched(self, store):
        _add(store, id_="T1", assigned="dev-1", status="in_progress")
        _add(store, id_="T2", assigned="dev-1", status="in_progress")
        latest = {"T1": "dev-1", "T2": "dev-1"}
        wip = WipEnforcer(limit=2)
        # 2 active, limit 2 → cannot accept (must be < limit)
        assert wip.can_accept("dev-1", store, latest) is False

    def test_reject_reason_routes_through_same_logic(self, store):
        for i in range(3):
            _add(store, id_=f"T{i}", assigned="review", status="in_progress")
        latest = {f"T{i}": "dev-1" for i in range(3)}
        wip = WipEnforcer(limit=1)
        # Latest dispatched says no one is on review → can accept → no reason
        assert wip.reject_reason("review", store, latest) is None
        # Without latest_dispatched (legacy) → 3 on review → reject
        assert wip.reject_reason("review", store) is not None


class TestAssigneeEquivalence:
    """2026-06-10 review P1-8: workflow-resume emits task.dispatched keyed
    by bare role name ('dev') while dispatch queries by instance_id
    ('dev-1'); exact equality missed the in-flight task and double-
    dispatched into the same pane."""

    @staticmethod
    def _equiv(a: str, b: str) -> bool:
        roles = {("dev", "dev-1"), ("dev", "dev-2"), ("review", "review-1")}
        return a == b or (a, b) in roles or (b, a) in roles

    def test_role_name_keyed_dispatch_counts_against_instance(self, store):
        _add(store, id_="T1", assigned="dev-1", status="in_progress")
        latest = {"T1": "dev"}  # workflow-resume shape
        wip = WipEnforcer(limit=1)
        # Without equivalence: the bug — slot looks free
        assert wip.can_accept("dev-1", store, latest) is True
        # With equivalence: in-flight task occupies the slot
        assert wip.can_accept("dev-1", store, latest, equivalent=self._equiv) is False

    def test_unrelated_role_name_still_free(self, store):
        _add(store, id_="T1", assigned="dev-1", status="in_progress")
        latest = {"T1": "dev"}
        wip = WipEnforcer(limit=1)
        assert wip.can_accept("review-1", store, latest, equivalent=self._equiv) is True

    def test_reject_reason_threads_equivalence(self, store):
        _add(store, id_="T1", assigned="dev-1", status="in_progress")
        latest = {"T1": "dev"}
        wip = WipEnforcer(limit=1)
        assert wip.reject_reason(
            "dev-1", store, latest, equivalent=self._equiv,
        ) is not None
