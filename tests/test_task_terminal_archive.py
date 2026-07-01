"""Tests for kanban.json terminal-state archival (G-TASK-1)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class TestTerminalStateArchive:
    def test_done_task_moves_to_archive(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store.add(Task(id="T1", title="x", status="in_progress"))
        store.update("T1", status="done")

        # Active file no longer contains T1
        active_content = (tmp_path / "kanban.json").read_text()
        assert "T1" not in active_content

        # Archive file contains it
        archive = tmp_path / "kanban" / f"{_today()}.json"
        assert archive.exists()
        archived = json.loads(archive.read_text())
        assert any(t["id"] == "T1" for t in archived)

    def test_cancelled_task_moves_to_archive(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store.add(Task(id="T1", title="x"))
        store.update("T1", status="cancelled")

        archive = tmp_path / "kanban" / f"{_today()}.json"
        assert archive.exists()

    def test_non_terminal_updates_stay_active(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store.add(Task(id="T1", title="x", status="backlog"))
        store.update("T1", status="in_progress")
        store.update("T1", status="review")
        store.update("T1", status="testing")

        active = json.loads((tmp_path / "kanban.json").read_text())
        assert any(t["id"] == "T1" for t in active)
        assert not (tmp_path / "kanban").exists()

    def test_list_all_excludes_archived(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store.add(Task(id="T1", title="done-task", status="in_progress"))
        store.add(Task(id="T2", title="active-task"))
        store.update("T1", status="done")

        tasks = store.list_all()
        ids = [t.id for t in tasks]
        assert "T2" in ids
        assert "T1" not in ids

    def test_list_all_with_archive_includes_both(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store.add(Task(id="T1", title="done-task", status="in_progress"))
        store.add(Task(id="T2", title="active-task"))
        store.update("T1", status="done")

        tasks = store.list_all_with_archive()
        ids = {t.id for t in tasks}
        assert ids == {"T1", "T2"}

    def test_get_finds_archived(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store.add(Task(id="T1", title="x"))
        store.update("T1", status="done")

        task = store.get("T1")
        assert task is not None
        assert task.id == "T1"
        assert task.status == "done"

    def test_get_nonexistent_returns_none(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        assert store.get("ghost") is None

    def test_multiple_done_tasks_same_day_append(self, tmp_path: Path):
        store = TaskStore(tmp_path / "kanban.json")
        store.add(Task(id="T1", title="a"))
        store.add(Task(id="T2", title="b"))
        store.update("T1", status="done")
        store.update("T2", status="done")

        archive = tmp_path / "kanban" / f"{_today()}.json"
        archived = json.loads(archive.read_text())
        ids = {t["id"] for t in archived}
        assert ids == {"T1", "T2"}

    def test_blocked_by_resolves_with_archived_terminal(self, tmp_path: Path):
        """T-B blocked_by T-A. T-A is archived as done. T-B must be
        returned by ready() because T-A counts as terminal even though
        it's no longer in active kanban.json."""
        store = TaskStore(tmp_path / "kanban.json")
        store.add(Task(id="T1", title="prereq"))
        store.add(Task(id="T2", title="follow", blocked_by=["T1"]))
        store.update("T1", status="done")

        ready = store.ready()
        ready_ids = [t.id for t in ready]
        assert "T2" in ready_ids

    def test_terminal_index_persists(self, tmp_path: Path):
        """After archiving T1, the terminal index should record it so
        subsequent ready() calls don't need to rescan archives."""
        store = TaskStore(tmp_path / "kanban.json")
        store.add(Task(id="T1", title="x"))
        store.update("T1", status="done")

        index = tmp_path / "kanban-terminal-index.json"
        assert index.exists()
        data = json.loads(index.read_text())
        assert "T1" in data

    def test_list_all_with_archive_last_days(self, tmp_path: Path):
        """Optional last_days filter."""
        store = TaskStore(tmp_path / "kanban.json")
        store.add(Task(id="T1", title="x"))
        store.update("T1", status="done")
        # Today's archive present; last_days=1 should include it
        tasks = store.list_all_with_archive(last_days=1)
        ids = {t.id for t in tasks}
        assert "T1" in ids
