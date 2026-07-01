from __future__ import annotations

from pathlib import Path

from zf.runtime import process_guard
from zf.runtime.process_guard import SingleOwnerProcessGuard


def test_single_owner_guard_acquires_and_releases(tmp_path: Path) -> None:
    lock = tmp_path / "watcher.pid.json"
    guard = SingleOwnerProcessGuard(lock, owner_pid=4242)

    result = guard.acquire()

    assert result.acquired is True
    assert result.owner_pid == 4242
    assert lock.exists()
    assert guard.release() is True
    assert not lock.exists()


def test_single_owner_guard_refuses_live_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lock = tmp_path / "watcher.pid.json"
    lock.write_text('{"owner_pid": 1111}\n', encoding="utf-8")
    monkeypatch.setattr(process_guard, "_pid_alive", lambda pid: pid == 1111)

    result = SingleOwnerProcessGuard(lock, owner_pid=2222).acquire()

    assert result.acquired is False
    assert result.reason == "owner_alive"
    assert result.owner_pid == 1111

