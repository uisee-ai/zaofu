"""Tests for the per-session_id concurrent resume mutex (B3)."""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import pytest

from zf.runtime.session_mutex import SessionLock, SessionLockBusy


def test_acquire_and_release_via_context_manager(tmp_path: Path):
    lock_dir = tmp_path / "locks"
    with SessionLock(lock_dir, "abc-123"):
        # While held, the lock file exists
        assert (lock_dir / "abc-123.lock").exists()
    # After release, the lock file may persist (held by other instances) or be
    # cleaned up — we don't constrain that.


def test_second_acquire_in_same_process_raises(tmp_path: Path):
    lock_dir = tmp_path / "locks"
    first = SessionLock(lock_dir, "abc-123")
    first.__enter__()
    try:
        with pytest.raises(SessionLockBusy):
            with SessionLock(lock_dir, "abc-123"):
                pytest.fail("second acquire should have failed")
    finally:
        first.__exit__(None, None, None)


def test_different_session_ids_do_not_block(tmp_path: Path):
    lock_dir = tmp_path / "locks"
    with SessionLock(lock_dir, "abc"):
        with SessionLock(lock_dir, "def"):
            pass


def test_lock_releases_on_exception(tmp_path: Path):
    lock_dir = tmp_path / "locks"
    with pytest.raises(RuntimeError):
        with SessionLock(lock_dir, "abc"):
            raise RuntimeError("boom")
    # After the exception, a fresh acquire on the same id should succeed
    with SessionLock(lock_dir, "abc"):
        pass


def _hold_lock(lock_dir_str: str, sid: str, hold_seconds: float, ready_path: str) -> None:
    """Worker that grabs the lock, signals ready, then sleeps."""
    lock = SessionLock(Path(lock_dir_str), sid)
    lock.__enter__()
    Path(ready_path).touch()
    time.sleep(hold_seconds)
    lock.__exit__(None, None, None)


def test_cross_process_mutex(tmp_path: Path):
    """Two processes acquiring the same session_id must serialize."""
    lock_dir = tmp_path / "locks"
    ready = tmp_path / "ready.flag"
    p = multiprocessing.Process(
        target=_hold_lock, args=(str(lock_dir), "shared", 1.0, str(ready)),
    )
    p.start()
    try:
        # Wait for the worker to actually hold the lock
        for _ in range(50):
            if ready.exists():
                break
            time.sleep(0.02)
        assert ready.exists(), "worker never acquired the lock"
        with pytest.raises(SessionLockBusy):
            with SessionLock(lock_dir, "shared"):
                pytest.fail("acquired a held cross-process lock")
    finally:
        p.join(timeout=3)
        assert not p.is_alive()

    # After the worker releases, we can acquire
    with SessionLock(lock_dir, "shared"):
        pass
