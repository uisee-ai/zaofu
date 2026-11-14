"""Shared execution locks using the same physical identity as preparation."""
from contextlib import contextmanager
from collections.abc import Iterator
import fcntl
from pathlib import Path
import sys

from zf.core.state.locks import FileLock, lock_path_for
from zf.runtime import environment_exec
from zf.runtime.environment_preparation import environment_preparation_dir


class EnvironmentInUse(RuntimeError):
    """A provider cannot enter a workspace held by a mutating operation."""


def environment_lock_path(worktree: Path) -> Path:
    return lock_path_for(environment_preparation_dir(worktree) / "prepare")


@contextmanager
def environment_in_use(worktree: Path) -> Iterator[None]:
    path = environment_lock_path(worktree)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise EnvironmentInUse("environment preparation is active; worker must wait") from exc
        yield


def guarded_provider_argv(worktree: Path, argv: list[str]) -> list[str]:
    return [sys.executable, str(Path(environment_exec.__file__)),
            str(environment_lock_path(worktree)), "--", *argv]


@contextmanager
def environment_mutation(worktree: Path) -> Iterator[None]:
    with FileLock(environment_lock_path(worktree), timeout_seconds=0):
        yield
