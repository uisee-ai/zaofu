"""Own the process group of one temporary diagnostic command, including on resident loss."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    owner_pid = int(sys.argv[1])
    # Only the supervising resident creates this session. Never signal an inherited group.
    if os.getpgrp() != os.getpid() or os.getsid(0) != os.getpid() or os.getppid() != owner_pid:
        return 143
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    command = subprocess.Popen(sys.argv[2:])
    while not stopped and os.getppid() == owner_pid:
        status = command.poll()
        if status is not None:
            return status if status >= 0 else 128 - status
        time.sleep(0.1)
    # The mixed runner receives TERM and can close its owned runtime. Sessions
    # that the runtime itself detached are handled by stop_experiment_runtime.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.killpg(os.getpid(), signal.SIGTERM)
    time.sleep(1)
    os.killpg(os.getpid(), signal.SIGKILL)
    return 143


if __name__ == "__main__":
    raise SystemExit(main())
