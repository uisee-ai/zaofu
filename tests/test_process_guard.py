from __future__ import annotations

from pathlib import Path
import pytest

from zf.runtime import process_guard
from zf.runtime.process_guard import SingleOwnerProcessGuard


@pytest.mark.parametrize('state, alive', [('Z', False), ('X', False), ('S', True), ('R', True)])
def test_guard_distinguishes_unreaped_exit_from_live_process(monkeypatch, state, alive):
    monkeypatch.setattr(process_guard.os, 'kill', lambda *args: None)
    monkeypatch.setattr(Path, 'read_text', lambda self: f'4321 (name with ) parens) {state} 1 2 3')
    assert process_guard._pid_alive(4321) is alive


def test_guard_keeps_live_probe_when_procfs_is_unavailable(monkeypatch):
    monkeypatch.setattr(process_guard.os, 'kill', lambda *args: None)
    def unavailable(self):
        raise PermissionError('procfs unavailable')
    monkeypatch.setattr(Path, 'read_text', unavailable)
    assert process_guard._pid_alive(4321)


def test_single_owner_guard_acquires_and_releases(tmp_path: Path) -> None:
    lock = tmp_path / "watcher.pid.json"
    guard = SingleOwnerProcessGuard(lock, owner_pid=4242)

    result = guard.acquire()

    assert result.acquired is True
    assert result.owner_pid == 4242
    assert lock.exists()
    assert guard.release() is True
    assert not lock.exists()


def test_single_owner_guard_records_epoch_and_refreshes_heartbeat(tmp_path: Path) -> None:
    lock = tmp_path / "watcher.pid.json"
    guard = SingleOwnerProcessGuard(lock, owner_pid=4242, component="watcher")

    result = guard.acquire()
    first = guard._read_lock()
    assert result.epoch
    assert first["epoch"] == result.epoch
    assert first["component"] == "watcher"

    assert guard.heartbeat() is True
    refreshed = guard._read_lock()
    assert refreshed["epoch"] == result.epoch
    assert refreshed["heartbeat_at"]


def test_single_owner_guard_new_epoch_replaces_dead_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lock = tmp_path / "watcher.pid.json"
    lock.write_text(
        '{"owner_pid": 1111, "epoch": "old-epoch", "component": "watcher"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(process_guard, "_pid_alive", lambda _pid: False)

    result = SingleOwnerProcessGuard(lock, owner_pid=2222).acquire()

    assert result.acquired is True
    assert result.epoch != "old-epoch"
    assert SingleOwnerProcessGuard(lock, owner_pid=2222)._read_lock()["epoch"] == result.epoch


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
