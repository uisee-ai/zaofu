"""Run one repair command in a private Linux subreaper and drain descendants.

The helper is a process boundary, not a filesystem sandbox. It owns only its
descendants, including children that create a new session or double-fork.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time


class RepairProcessCleanupError(OSError):
    """The command's descendant shutdown could not be proved."""


def run_repair_command(argv: list[str], *, cwd: str, env: dict[str, str], timeout: float,
                       capture_output: bool = False, text: bool = False,
                       stdout=None, stderr=None, check: bool = False) -> subprocess.CompletedProcess:
    if sys.platform != 'linux':
        raise OSError(errno.ENOTSUP, 'repair subprocess supervision requires Linux pidfd/subreaper')
    with TemporaryDirectory(prefix='zf-repair-process-') as directory:
        request = Path(directory) / 'request.json'
        result = Path(directory) / 'result.json'
        request.write_text(json.dumps({'argv': argv, 'timeout': timeout, 'owner_pid': os.getpid()}))
        try:
            completed = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()),
                '--request', str(request), '--result', str(result)], cwd=cwd, env=env,
                capture_output=capture_output, text=text, stdout=stdout, stderr=stderr,
                timeout=timeout + 10, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RepairProcessCleanupError('repair supervisor expired; cleanup unknown') from exc
        if completed.returncode or not result.exists():
            raise RepairProcessCleanupError('repair supervisor outcome unknown')
        try:
            receipt = json.loads(result.read_text())
        except (ValueError, OSError) as exc:
            raise RepairProcessCleanupError('repair supervisor receipt unavailable') from exc
        if (not isinstance(receipt, dict) or receipt.get('status') not in {
                'completed', 'cancelled', 'timeout', 'spawn_failed'}
                or not isinstance(receipt.get('returncode'), int)):
            raise RepairProcessCleanupError('repair supervisor receipt invalid')
        if receipt.get('cleanup') != 'complete':
            raise RepairProcessCleanupError('repair descendant cleanup outcome unknown')
        if receipt.get('status') == 'timeout':
            error = subprocess.TimeoutExpired(argv, timeout, output=completed.stdout, stderr=completed.stderr)
            error.cleanup_receipt = receipt
            raise error
        if receipt.get('status') == 'spawn_failed':
            raise OSError(str(receipt.get('reason') or 'repair command could not start'))
        outcome = subprocess.CompletedProcess(argv, receipt['returncode'], completed.stdout, completed.stderr)
        outcome.cleanup_receipt = receipt
        if check:
            outcome.check_returncode()
        return outcome


def _stat(pid: int) -> tuple[int, int, str] | None:
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rpartition(') ')[2].split()
        return int(fields[1]), int(fields[19]), fields[0]
    except (OSError, ValueError, IndexError):
        return None


def _descendants() -> dict[int, tuple[int, int, str]]:
    # Kill only verified direct children. Once each parent exits, the subreaper
    # adopts its orphans and the next round can safely address those children.
    rows = {}
    for value in Path(f'/proc/self/task/{os.getpid()}/children').read_text().split():
        pid = int(value)
        row = _stat(pid)
        if row is not None and row[0] == os.getpid():
            rows[pid] = row
    return rows


def _pidfd_open(pid: int) -> int:
    if hasattr(os, 'pidfd_open'):
        return os.pidfd_open(pid)
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, 'pidfd_open'):
        raise OSError(errno.ENOTSUP, 'libc pidfd_open unavailable')
    libc.pidfd_open.argtypes = [ctypes.c_int, ctypes.c_uint]
    libc.pidfd_open.restype = ctypes.c_int
    fd = libc.pidfd_open(pid, 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), 'pidfd_open failed')
    return fd


def _pidfd_signal(fd: int, sig: int) -> None:
    if hasattr(signal, 'pidfd_send_signal'):
        signal.pidfd_send_signal(fd, sig)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, 'pidfd_send_signal'):
        raise OSError(errno.ENOTSUP, 'libc pidfd_send_signal unavailable')
    libc.pidfd_send_signal.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    libc.pidfd_send_signal.restype = ctypes.c_int
    if libc.pidfd_send_signal(fd, sig, None, 0) != 0:
        raise OSError(ctypes.get_errno(), 'pidfd_send_signal failed')


def _signal(pid: int, birth: int, sig: int) -> bool:
    try:
        fd = _pidfd_open(pid)
    except ProcessLookupError:
        return False
    try:
        current = _stat(pid)
        if current is None or current[0] != os.getpid() or current[1] != birth:
            return False
        _pidfd_signal(fd, sig)
        return True
    except ProcessLookupError:
        return False
    finally:
        os.close(fd)


def _drain(process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + 3
    kill_at = time.monotonic() + 1
    sent = set()
    while True:
        process.poll()  # Preserve the command's exit status before reaping orphans.
        children = _descendants()
        for pid, (parent, _birth, state) in children.items():
            if parent == os.getpid() and pid != process.pid and state == 'Z':
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass
        children = _descendants()
        direct = Path(f'/proc/self/task/{os.getpid()}/children').read_text().split()
        if not children and not direct:
            return {'cleanup': 'complete', 'descendants_signalled': len({pid for pid, _, _ in sent})}
        if time.monotonic() >= deadline:
            return {'cleanup': 'unknown', 'remaining_pids': sorted(set(children) | {int(pid) for pid in direct})}
        sig = signal.SIGKILL if time.monotonic() >= kill_at else signal.SIGTERM
        for pid, (_parent, birth, state) in children.items():
            key = (pid, birth, sig)
            if state != 'Z' and key not in sent and _signal(pid, birth, sig):
                sent.add(key)
        time.sleep(0.02)


def supervise(request: dict) -> dict:
    # PR_SET_CHILD_SUBREAPER is process-local: never alter the resident controller.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'cannot establish repair subreaper')
    stopped = False
    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        process = subprocess.Popen(request['argv'], start_new_session=True, stdin=subprocess.DEVNULL)
    except OSError as exc:
        return {'status': 'spawn_failed', 'returncode': 127, 'reason': str(exc), 'cleanup': 'complete'}
    deadline = time.monotonic() + float(request['timeout'])
    status = 'completed'
    while process.poll() is None:
        if stopped or os.getppid() != request['owner_pid']:
            status = 'cancelled'
            break
        if time.monotonic() >= deadline:
            status = 'timeout'
            break
        time.sleep(0.02)
    cleanup = _drain(process)
    return {'method': 'linux-subreaper-pidfd.v1', 'status': status,
            'supervisor_pid': os.getpid(), 'command_pid': process.pid,
            'returncode': 124 if status == 'timeout' else 143 if status == 'cancelled' else process.returncode,
            **cleanup}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    parser.add_argument('--result', required=True)
    args = parser.parse_args()
    result = supervise(json.loads(Path(args.request).read_text()))
    temporary = Path(args.result).with_suffix('.pending')
    temporary.write_text(json.dumps(result) + '\n')
    temporary.replace(args.result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
