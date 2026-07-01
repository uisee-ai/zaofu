"""Tests for blocked_by transitive resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


class TestReadyResolution:
    def test_ready_returns_unblocked_backlog(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store._save_raw([])
        t1 = Task(title="A", id="T1")
        store.add(t1)
        ready = store.ready()
        assert len(ready) == 1
        assert ready[0].id == "T1"

    def test_ready_excludes_blocked(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store._save_raw([])
        t1 = Task(title="A", id="T1")
        store.add(t1)
        t2 = Task(title="B", id="T2", blocked_by=["T1"])
        store.add(t2)
        ready = store.ready()
        assert len(ready) == 1
        assert ready[0].id == "T1"

    def test_ready_includes_after_blocker_done(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store._save_raw([])
        t1 = Task(title="A", id="T1")
        store.add(t1)
        t2 = Task(title="B", id="T2", blocked_by=["T1"])
        store.add(t2)
        store.update("T1", status="done")
        ready = store.ready()
        ids = {t.id for t in ready}
        assert "T2" in ids

    def test_ready_transitive(self, tmp_path: Path):
        """T3 blocked by T2, T2 blocked by T1. Only T1 is ready."""
        store = TaskStore(tmp_path / "kanban.json")
        store._save_raw([])
        store.add(Task(title="A", id="T1"))
        store.add(Task(title="B", id="T2", blocked_by=["T1"]))
        store.add(Task(title="C", id="T3", blocked_by=["T2"]))
        ready = store.ready()
        assert len(ready) == 1
        assert ready[0].id == "T1"

    def test_ready_after_chain_complete(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store._save_raw([])
        store.add(Task(title="A", id="T1"))
        store.add(Task(title="B", id="T2", blocked_by=["T1"]))
        store.add(Task(title="C", id="T3", blocked_by=["T2"]))
        store.update("T1", status="done")
        store.update("T2", status="done")
        ready = store.ready()
        ids = {t.id for t in ready}
        assert "T3" in ids

    def test_cancelled_also_releases(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store._save_raw([])
        store.add(Task(title="A", id="T1"))
        store.add(Task(title="B", id="T2", blocked_by=["T1"]))
        store.update("T1", status="cancelled")
        ready = store.ready()
        ids = {t.id for t in ready}
        assert "T2" in ids
