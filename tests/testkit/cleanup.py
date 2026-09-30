"""Resource cleanup assertions for ZaoFu tests."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ResourceSnapshot:
    """Resources owned by a TestKit scope at one point in time."""

    child_pids: frozenset[int]
    lock_files: frozenset[str]
    temporary_files: frozenset[str]


def resource_snapshot(root: Path) -> ResourceSnapshot:
    """Capture process and transient-file state below an isolated scope.

    The helper intentionally does not inspect or mutate paths outside ``root``;
    callers can therefore use it in parallel tests without treating another
    project or the developer's workspace as a leak.
    """
    resolved = root.resolve()
    lock_files: set[str] = set()
    temporary_files: set[str] = set()
    if resolved.exists():
        for path in resolved.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(resolved).as_posix()
            if path.name.endswith(".lock") or path.name.endswith(".lockfile"):
                lock_files.add(relative)
            if path.name.endswith(".tmp") or path.name.startswith(".") and ".tmp" in path.name:
                temporary_files.add(relative)
    return ResourceSnapshot(
        child_pids=frozenset(child_processes()),
        lock_files=frozenset(lock_files),
        temporary_files=frozenset(temporary_files),
    )


def assert_no_resource_leaks(
    before: ResourceSnapshot,
    *,
    root: Path,
) -> None:
    """Fail when a TestKit scope gained a process, lock, or temp artifact."""
    after = resource_snapshot(root)
    leaked_pids = sorted(after.child_pids - before.child_pids)
    leaked_locks = sorted(after.lock_files - before.lock_files)
    leaked_temporary = sorted(after.temporary_files - before.temporary_files)
    assert not leaked_pids, f"child processes leaked: {leaked_pids}"
    assert not leaked_locks, f"lock files leaked: {leaked_locks}"
    assert not leaked_temporary, f"temporary files leaked: {leaked_temporary}"


def terminate_process(
    process: subprocess.Popen[object],
    *,
    timeout: float = 5.0,
) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def terminate_process_group(
    process: subprocess.Popen[object],
    *,
    timeout: float = 5.0,
) -> None:
    """Terminate a TestKit-owned process and its descendants on POSIX."""
    if os.name != "posix":  # pragma: no cover - Windows uses job objects elsewhere
        terminate_process(process, timeout=timeout)
        return
    # Do not use ``poll()`` as an early return: the group leader may have
    # exited while a descendant is still alive (and still owns inherited
    # resources).  ``start_new_session=True`` makes the leader PID the PGID.
    try:
        os.killpg(process.pid, 15)
    except ProcessLookupError:
        if process.poll() is None:
            process.wait(timeout=timeout)
        return

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)

    # A descendant may ignore SIGTERM after the leader has already exited.
    # Escalate based on group existence, not the leader's return code.
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        pass
    else:
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.wait(timeout=max(timeout, 0.1))


def child_processes() -> list[int]:
    """Return direct child PIDs without creating an observer subprocess.

    Invoking ``ps`` here would itself create a short-lived child and can race
    with the leak assertion.  Linux exposes the parent PID in ``/proc``;
    callers on other hosts receive an empty best-effort result.
    """
    proc_root = Path("/proc")
    if not proc_root.exists():
        return []
    parent_pid = os.getpid()
    children: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text(encoding="utf-8").split()
            if len(fields) > 3 and int(fields[3]) == parent_pid:
                children.append(int(entry.name))
        except (OSError, ValueError):
            continue
    return children


def assert_no_child_process_leaks(before: Iterable[int]) -> None:
    baseline = set(before)
    leaked = sorted(set(child_processes()) - baseline)
    assert not leaked, f"child processes leaked: {leaked}"


def remove_empty_temp_files(root: Path) -> None:
    for path in root.rglob("*.tmp"):
        if path.is_file() and path.stat().st_size == 0:
            path.unlink()
