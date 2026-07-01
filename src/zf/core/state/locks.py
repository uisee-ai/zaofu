"""Advisory file locks for runtime state files."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar


T = TypeVar("T")


def lock_path_for(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


class FileLock:
    """Small POSIX advisory lock wrapper.

    The lock file is intentionally left in place; the kernel lock on the
    open file descriptor is the synchronization primitive.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None


@contextmanager
def locked_path(path: Path) -> Iterator[None]:
    with FileLock(lock_path_for(path)):
        yield


def locked_read_modify_write(path: Path, fn: Callable[[], T]) -> T:
    with locked_path(path):
        return fn()
