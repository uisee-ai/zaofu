"""External control-plane restart actuator for verified source repair."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import time
from pathlib import Path

from zf.runtime.cli_command import zf_cli_cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    state_dir = Path(args.state_dir).resolve()
    time.sleep(max(float(args.delay_seconds), 0.1))
    watcher_pid = _recorded_watcher_pid(state_dir)
    if watcher_pid > 0 and watcher_pid != os.getpid():
        _terminate_watcher(watcher_pid)
    os.chdir(project_root)
    command = [
        *shlex.split(zf_cli_cmd()),
        "start",
        "--control-plane-only",
    ]
    os.execvp(command[0], command)
    return 1


def _recorded_watcher_pid(state_dir: Path) -> int:
    path = state_dir / "processes" / "watcher.pid.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    try:
        pid = int(payload.get("owner_pid") or payload.get("pid") or 0)
    except (TypeError, ValueError):
        return 0
    if pid <= 0 or not _looks_like_watcher(pid):
        return 0
    return pid


def _looks_like_watcher(pid: int) -> bool:
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
    except OSError:
        return False
    return b"zf" in command and b"start" in command


def _terminate_watcher(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 15.0
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    if _looks_like_watcher(pid):
        os.kill(pid, signal.SIGKILL)


if __name__ == "__main__":
    raise SystemExit(main())
