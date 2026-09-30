"""Small subprocess lifecycle helpers for ZaoFu recovery tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
import os
import subprocess
import time

from .cleanup import terminate_process_group


@dataclass
class ManagedProcess:
    process: subprocess.Popen[str]
    command: tuple[str, ...]
    cwd: Path

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.poll()

    def stop(self, *, timeout: float = 5.0) -> None:
        terminate_process_group(self.process, timeout=timeout)

    def wait(self, *, timeout: float = 5.0) -> int:
        return self.process.wait(timeout=timeout)


def start_process(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> ManagedProcess:
    if not command:
        raise ValueError("command must not be empty")
    process = subprocess.Popen(
        [str(item) for item in command],
        cwd=cwd,
        env={**os.environ, **dict(env or {})},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=(os.name == "posix"),
    )
    return ManagedProcess(process=process, command=tuple(command), cwd=cwd)


def wait_for_exit(
    managed: ManagedProcess,
    *,
    timeout: float,
) -> int:
    deadline = time.monotonic() + timeout
    while managed.returncode is None and time.monotonic() < deadline:
        time.sleep(0.01)
    if managed.returncode is None:
        raise TimeoutError(f"process did not exit within {timeout}s: {managed.pid}")
    return managed.returncode
