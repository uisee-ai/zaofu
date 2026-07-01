"""Tests for WIP enforcement."""

from __future__ import annotations

from pathlib import Path

from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.task.wip import WipEnforcer


class TestWipEnforcer:
    def test_can_accept_when_idle(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store._save_raw([])
        enforcer = WipEnforcer()
        assert enforcer.can_accept("dev-1", store) is True

    def test_cannot_accept_when_at_limit(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        task = Task(title="Active", status="in_progress", assigned_to="dev-1")
        store.add(task)
        enforcer = WipEnforcer(limit=1)
        assert enforcer.can_accept("dev-1", store) is False

    def test_can_accept_different_worker(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        task = Task(title="Active", status="in_progress", assigned_to="dev-1")
        store.add(task)
        enforcer = WipEnforcer(limit=1)
        assert enforcer.can_accept("dev-2", store) is True

    def test_reject_reason(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        task = Task(title="Active", status="in_progress", assigned_to="dev-1")
        store.add(task)
        enforcer = WipEnforcer(limit=1)
        reason = enforcer.reject_reason("dev-1", store)
        assert reason is not None
        assert "dev-1" in reason

    def test_no_reject_reason_when_idle(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store._save_raw([])
        enforcer = WipEnforcer()
        assert enforcer.reject_reason("dev-1", store) is None
