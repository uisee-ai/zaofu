"""Execution and mutation fences cover the workspace and declared resources."""
from contextlib import contextmanager
from collections.abc import Iterator
import fcntl
import json
from pathlib import Path
import sys

from zf.core.state.locks import lock_path_for
from zf.runtime import environment_exec
from zf.runtime.environment_preparation import environment_preparation_dir
from zf.runtime.environment_resources import environment_lock_plan


class EnvironmentInUse(RuntimeError):
    """A provider cannot enter resources held by a mutating operation."""


def environment_lock_path(worktree: Path) -> Path:
    return lock_path_for(environment_preparation_dir(worktree) / 'prepare')


@contextmanager
def environment_in_use(worktree: Path) -> Iterator[None]:
    try:
        with environment_exec.resource_locks(environment_lock_plan(worktree), fcntl.LOCK_SH):
            yield
    except BlockingIOError as exc:
        raise EnvironmentInUse('environment preparation is active; worker must wait') from exc


def guarded_provider_argv(worktree: Path, argv: list[str]) -> list[str]:
    return [sys.executable, str(Path(environment_exec.__file__)),
            json.dumps(environment_lock_plan(worktree)), '--', *argv]


@contextmanager
def environment_mutation(worktree: Path) -> Iterator[None]:
    try:
        with environment_exec.resource_locks(environment_lock_plan(worktree), fcntl.LOCK_EX):
            yield
    except BlockingIOError as exc:
        raise TimeoutError('environment resource in use; wait for holders to exit') from exc
