"""Tests for TaskStore backed by kanban.json."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore


def test_create_and_list(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    t = store.add(Task(title="Task A"))
    tasks = store.list_all()
    assert len(tasks) == 1
    assert tasks[0].id == t.id


def test_save_is_atomic_no_partial_writes(tmp_path: Path, monkeypatch):
    """If write is interrupted between os.replace and any partial state,
    the kanban file is either the old version or the new version — never half.
    """
    import os
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(title="A"))
    snapshot_before = (tmp_path / "kanban.json").read_text()

    real_replace = os.replace
    calls: list[tuple] = []

    def fail_replace(src, dst):
        calls.append((src, dst))
        raise OSError("simulated mid-write crash")

    monkeypatch.setattr("os.replace", fail_replace)
    with pytest.raises(OSError):
        store.add(Task(title="B"))

    monkeypatch.setattr("os.replace", real_replace)
    snapshot_after = (tmp_path / "kanban.json").read_text()
    assert snapshot_after == snapshot_before, "kanban.json was corrupted by partial write"
    # verify temp files cleaned up
    assert not list(tmp_path.glob("kanban.json.*tmp*")), "temp files left behind"
    assert calls, "os.replace was never called — store is not using atomic write"


def test_save_uses_temp_file_then_replace(tmp_path: Path, monkeypatch):
    """Atomic write should call os.replace(src=temp_path, dst=target)
    where src is a sibling temp file, not the target itself.
    """
    import os
    store = TaskStore(tmp_path / "kanban.json")
    seen: list[tuple[str, str]] = []
    real_replace = os.replace

    def tracking_replace(src, dst):
        seen.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("os.replace", tracking_replace)
    store.add(Task(title="A"))
    assert seen, "os.replace was never called"
    src, dst = seen[-1]
    assert dst == str(tmp_path / "kanban.json")
    assert src != dst
    assert Path(src).parent == tmp_path  # sibling temp, same dir


def test_add_multiple(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(title="A"))
    store.add(Task(title="B"))
    store.add(Task(title="C"))
    assert len(store.list_all()) == 3


def test_get_by_id(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    t = store.add(Task(title="Find me"))
    found = store.get(t.id)
    assert found is not None
    assert found.title == "Find me"


def test_get_missing_returns_none(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    assert store.get("nonexistent") is None


def test_update_status(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    t = store.add(Task(title="Move me"))
    store.update(t.id, status="in_progress")
    updated = store.get(t.id)
    assert updated.status == "in_progress"


def test_filter_by_status(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(title="A", status="backlog"))
    store.add(Task(title="B", status="in_progress"))
    store.add(Task(title="C", status="backlog"))
    backlog = store.filter(status="backlog")
    assert len(backlog) == 2


def test_ensure_by_key_creates(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    t = store.ensure(key="auth:jwt", title="JWT auth")
    assert t.key == "auth:jwt"
    assert len(store.list_all()) == 1


def test_ensure_by_key_idempotent(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    t1 = store.ensure(key="auth:jwt", title="JWT auth")
    t2 = store.ensure(key="auth:jwt", title="JWT auth updated")
    assert t1.id == t2.id
    assert len(store.list_all()) == 1
    # Title gets updated on re-ensure
    assert store.get(t1.id).title == "JWT auth updated"


def test_blocked_by_validation(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    t1 = store.add(Task(title="Foundation"))
    t2 = store.add(Task(title="Dependent", blocked_by=[t1.id]))
    assert t2.blocked_by == [t1.id]


def test_blocked_by_invalid_ref_raises(tmp_path: Path):
    store = TaskStore(tmp_path / "kanban.json")
    with pytest.raises(ValueError, match="blocked_by"):
        store.add(Task(title="Bad ref", blocked_by=["nonexistent-id"]))


def test_persistence_across_instances(tmp_path: Path):
    path = tmp_path / "kanban.json"
    store1 = TaskStore(path)
    store1.add(Task(title="Persist me"))

    store2 = TaskStore(path)
    assert len(store2.list_all()) == 1
    assert store2.list_all()[0].title == "Persist me"
