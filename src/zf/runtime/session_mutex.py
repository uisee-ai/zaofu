"""Per-session_id concurrent resume mutex.

Two simultaneous `claude -p --resume <same-id>` invocations can corrupt the
session JSONL under ~/.claude/projects/. SessionLock guards against this
with an fcntl.flock on a sibling lock file. Each session_id gets its own
lock file at .zf/locks/sessions/<id>.lock; different ids do not block.

Usage:

    with SessionLock(state_dir / "locks" / "sessions", session_id):
        # safe to spawn `claude -p --resume <session_id>`
        ...

If the lock is held by another process or another instance in the same
process, SessionLockBusy is raised immediately (non-blocking).
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Any


class SessionLockBusy(Exception):
    pass


class SessionLock:
    def __init__(self, lock_dir: Path, session_id: str) -> None:
        self.lock_dir = lock_dir
        self.session_id = session_id
        self.lock_path = lock_dir / f"{session_id}.lock"
        self._fh: Any = None

    def __enter__(self) -> "SessionLock":
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._fh = self.lock_path.open("w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            self._fh.close()
            self._fh = None
            raise SessionLockBusy(
                f"session {self.session_id!r} is already locked"
            ) from e
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
        # Best-effort cleanup; another waiting process may immediately
        # recreate the file, which is fine.
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass
