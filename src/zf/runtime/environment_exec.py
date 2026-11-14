"""Exec a native provider with an inherited environment lock (stdlib only)."""
import fcntl
import os
from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        return 64
    path = Path(sys.argv[1])
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Environment preparation is active; provider launch must wait.", file=sys.stderr)
            return 75
        os.set_inheritable(descriptor, True)
        os.execvp(sys.argv[3], sys.argv[3:])
    finally:
        os.close(descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
