"""Linux parent-death guard; exec one non-daemonizing owned command."""

from __future__ import annotations

import ctypes
import os
import signal
import sys


def main() -> None:
    expected_parent = int(sys.argv[1])
    command = sys.argv[2:]
    if sys.platform != "linux" or not command or expected_parent <= 1:
        raise SystemExit("Linux, an owned parent and an explicit command are required")
    if os.getppid() != expected_parent:
        raise SystemExit("parent changed before guard setup")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if os.getppid() != expected_parent:
        raise SystemExit("parent changed during guard setup")
    os.execv(command[0], command)


if __name__ == "__main__":
    main()
