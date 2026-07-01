"""Tests for SessionStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.state.session import SessionStore, SessionState, ZfNotInitialized


def test_worker_state_persists_across_load(tmp_path: Path):
    from zf.core.state.session import WorkerState
    store = SessionStore(tmp_path / "session.yaml")
    store.create(project_root=str(tmp_path))
    store.upsert_worker(WorkerState(
        role="dev",
        state="working",
        session_id="abc-123",
        turn_count=5,
        consecutive_failures=1,
        last_dispatch="T1",
    ))
    reloaded = store.load()
    workers = {w["role"]: w for w in reloaded.workers}
    assert "dev" in workers
    assert workers["dev"]["turn_count"] == 5
    assert workers["dev"]["consecutive_failures"] == 1
    assert workers["dev"]["session_id"] == "abc-123"


def test_upsert_worker_overwrites_same_role(tmp_path: Path):
    from zf.core.state.session import WorkerState
    store = SessionStore(tmp_path / "session.yaml")
    store.create(project_root=str(tmp_path))
    store.upsert_worker(WorkerState(role="dev", state="working", turn_count=1))
    store.upsert_worker(WorkerState(role="dev", state="idle", turn_count=2))
    reloaded = store.load()
    devs = [w for w in reloaded.workers if w["role"] == "dev"]
    assert len(devs) == 1
    assert devs[0]["turn_count"] == 2
    assert devs[0]["state"] == "idle"


def test_get_worker_returns_typed_state(tmp_path: Path):
    from zf.core.state.session import WorkerState
    store = SessionStore(tmp_path / "session.yaml")
    store.create(project_root=str(tmp_path))
    store.upsert_worker(WorkerState(role="dev", state="working", turn_count=3))
    w = store.get_worker("dev")
    assert isinstance(w, WorkerState)
    assert w.turn_count == 3


def test_get_worker_missing_returns_none(tmp_path: Path):
    store = SessionStore(tmp_path / "session.yaml")
    store.create(project_root=str(tmp_path))
    assert store.get_worker("nonexistent") is None


def test_create_session(tmp_path: Path):
    store = SessionStore(tmp_path / "session.yaml")
    state = store.create(project_root=str(tmp_path))
    assert state.session_id
    assert state.project_root == str(tmp_path)
    assert state.started_at
    assert store.path.exists()


def test_save_is_atomic_no_partial_writes(tmp_path: Path, monkeypatch):
    import os
    store = SessionStore(tmp_path / "session.yaml")
    store.create(project_root=str(tmp_path))
    snapshot_before = (tmp_path / "session.yaml").read_text()

    def fail_replace(src, dst):
        raise OSError("simulated mid-write crash")

    monkeypatch.setattr("os.replace", fail_replace)
    with pytest.raises(OSError):
        store.update(runtime_state="active")

    monkeypatch.undo()
    snapshot_after = (tmp_path / "session.yaml").read_text()
    assert snapshot_after == snapshot_before, "session.yaml was corrupted by partial write"
    assert not list(tmp_path.glob("session.yaml.*tmp*"))


def test_load_session(tmp_path: Path):
    store = SessionStore(tmp_path / "session.yaml")
    original = store.create(project_root="/some/path")
    loaded = store.load()
    assert loaded.session_id == original.session_id
    assert loaded.project_root == original.project_root


def test_load_missing_file_raises(tmp_path: Path):
    store = SessionStore(tmp_path / "session.yaml")
    with pytest.raises(ZfNotInitialized):
        store.load()


def test_update_session(tmp_path: Path):
    store = SessionStore(tmp_path / "session.yaml")
    store.create(project_root="/test")
    store.update(runtime_state="running")
    loaded = store.load()
    assert loaded.runtime_state == "running"


def test_update_latest_event_offset(tmp_path: Path):
    store = SessionStore(tmp_path / "session.yaml")
    store.create(project_root="/test")
    store.update(latest_event_offset=42)
    loaded = store.load()
    assert loaded.latest_event_offset == 42


def test_session_roundtrip_preserves_fields(tmp_path: Path):
    store = SessionStore(tmp_path / "session.yaml")
    state = store.create(project_root="/root")
    store.update(runtime_state="active", latest_event_offset=10)
    loaded = store.load()
    assert loaded.session_id == state.session_id
    assert loaded.started_at == state.started_at
    assert loaded.runtime_state == "active"
    assert loaded.latest_event_offset == 10
