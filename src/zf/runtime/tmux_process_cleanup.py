"""Linux cleanup of observed pane descendants, including tools with their own SID."""

from contextlib import ExitStack
import ctypes
import errno
import os
from pathlib import Path
import select
import signal
import sys
import time


def _identity(pid: int) -> tuple[int, str] | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[1]), fields[19]  # ppid, birth ticks; comm may contain spaces/parentheses.
    except (FileNotFoundError, ProcessLookupError):
        return None


def _processes() -> dict[int, tuple[int, str]]:
    rows = {}
    for path in Path("/proc").iterdir():
        if path.name.isdecimal():
            try:
                identity = _identity(int(path.name))
            except PermissionError:
                continue  # Unreadable foreign processes cannot be adopted as owned.
            if identity is not None:
                rows[int(path.name)] = identity
    return rows


def _descendants(rows: dict[int, tuple[int, str]], roots: set[int]) -> list[int]:
    children: dict[int, list[int]] = {}
    for pid, (parent, _) in rows.items():
        children.setdefault(parent, []).append(pid)
    pending, seen, found = list(roots), set(roots), []
    while pending:
        parent = pending.pop(0)
        for pid in children.get(parent, []):
            if pid not in seen:
                seen.add(pid)
                pending.append(pid)
                found.append(pid)
    if os.getpid() in seen:
        raise RuntimeError("refusing pane cleanup containing the calling process")
    return list(reversed(found))


class _Pidfd:
    """Use libc when the installed Python was built without pidfd wrappers."""

    def __init__(self):
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.pidfd_open.argtypes = [ctypes.c_int, ctypes.c_uint]
        self.libc.pidfd_open.restype = ctypes.c_int
        self.libc.pidfd_send_signal.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
        self.libc.pidfd_send_signal.restype = ctypes.c_int

    def open(self, pid: int) -> int:
        descriptor = self.libc.pidfd_open(pid, 0)
        if descriptor < 0:
            raise OSError(ctypes.get_errno(), "pidfd_open failed")
        return descriptor

    def send(self, descriptor: int, signum: int) -> None:
        if self.libc.pidfd_send_signal(descriptor, signum, None, 0) < 0:
            error = ctypes.get_errno()
            if error != errno.ESRCH:
                raise OSError(error, "pidfd_send_signal failed")


def _wait(handles: list[int], seconds: float) -> list[int]:
    deadline = time.monotonic() + seconds
    pending = list(handles)
    while pending:
        ready, _, _ = select.select(pending, [], [], max(0, deadline - time.monotonic()))
        pending = [handle for handle in pending if handle not in ready]
        if time.monotonic() >= deadline:
            break
    return pending


def terminate_pane_descendants(roots: set[int]) -> None:
    """Pin a live tree before pane teardown; never signal numeric PIDs or PGIDs.

    This closes observed descendants, not already-detached/unattributable daemons
    or adversarial forks after the snapshot. Other platforms keep tmux teardown.
    """
    if sys.platform != "linux" or not roots:
        return
    if any(pid <= 1 for pid in roots):
        raise ValueError("invalid pane root PID")
    rows = _processes()
    children = _descendants(rows, roots)
    if not children:
        return
    api = _Pidfd()
    with ExitStack() as stack:
        handles = []
        # Pin all before sending signals: parents exiting cannot make us lose
        # the handles for their already-observed separate-session descendants.
        for pid in children:
            try:
                descriptor = api.open(pid)
            except ProcessLookupError:
                continue
            stack.callback(os.close, descriptor)
            if _identity(pid) != rows[pid]:
                continue  # PID reuse / changed ancestry during capture: no authority.
            handles.append(descriptor)
        for descriptor in handles:
            api.send(descriptor, signal.SIGTERM)
        remaining = _wait(handles, 1.0)
        for descriptor in remaining:
            api.send(descriptor, signal.SIGKILL)
        if _wait(remaining, 1.0):
            raise RuntimeError("pane descendants did not exit after TERM/KILL")
