"""Managed Autoresearch resident sidecar for ``zf start``.

The sidecar owns only process lifecycle. Loop semantics stay in
``zf autoresearch resident`` and the existing Autoresearch event contracts.
"""

from __future__ import annotations

import json
import fcntl
import os
import signal
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.cli_command import zf_cli_cmd


@dataclass
class AutoresearchResidentSidecar:
    process: subprocess.Popen
    log_handle: Any
    pid_path: Path
    log_path: Path
    lock_handle: Any | None = None


def build_autoresearch_resident_command(
    *,
    state_dir: Path,
    interval_seconds: float,
    max_actions_per_tick: int,
    worktree_root: Path,
    output_root: Path | None = None,
    self_repair_consumer: bool = False,
    self_repair_spawn: bool = False,
    self_repair_backend: str = "",
) -> list[str]:
    command = [
        *shlex.split(zf_cli_cmd()),
        "autoresearch",
        "resident",
        "--watch",
        "--execute",
        "--state-dir",
        str(state_dir),
        "--interval-seconds",
        str(interval_seconds),
        "--max-actions-per-tick",
        str(max_actions_per_tick),
        "--worktree-root",
        str(worktree_root),
    ]
    if output_root is not None:
        command.extend(["--output-root", str(output_root)])
    if self_repair_consumer:
        command.append("--self-repair-consumer")
    if self_repair_spawn:
        command.append("--self-repair-spawn")
    if self_repair_backend:
        command.extend(["--self-repair-backend", self_repair_backend])
    return command


def start_autoresearch_resident_sidecar(
    *,
    config: object,
    state_dir: Path,
    project_root: Path,
    event_log: Any | None = None,
    dry_run: bool = False,
) -> AutoresearchResidentSidecar | None:
    resident = getattr(getattr(config, "runtime", None), "autoresearch_resident", None)
    if not resident or not bool(getattr(resident, "enabled", False)):
        return None

    try:
        interval_seconds = float(getattr(resident, "interval_seconds", 10.0) or 10.0)
    except (TypeError, ValueError):
        interval_seconds = 10.0
    interval_seconds = max(interval_seconds, 0.1)
    try:
        max_actions_per_tick = int(getattr(resident, "max_actions_per_tick", 1) or 1)
    except (TypeError, ValueError):
        max_actions_per_tick = 1
    max_actions_per_tick = max(max_actions_per_tick, 1)
    worktree_root = Path(
        str(
            getattr(
                resident,
                "worktree_root",
                "/tmp/zaofu-autoresearch-resident/worktrees",
            )
            or "/tmp/zaofu-autoresearch-resident/worktrees"
        )
    ).expanduser()
    output_raw = str(getattr(resident, "output_root", "") or "").strip()
    output_root = Path(output_raw).expanduser() if output_raw else None
    logs_dir = state_dir / "logs"
    processes_dir = state_dir / "processes"
    logs_dir.mkdir(parents=True, exist_ok=True)
    processes_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "autoresearch-resident.log"
    pid_path = processes_dir / "autoresearch-resident.pid.json"
    lock_path = processes_dir / "autoresearch-resident.lock"
    command = build_autoresearch_resident_command(
        state_dir=state_dir.resolve(),
        interval_seconds=interval_seconds,
        max_actions_per_tick=max_actions_per_tick,
        worktree_root=worktree_root,
        output_root=output_root,
        self_repair_consumer=bool(getattr(resident, "self_repair_consumer", False)),
        self_repair_spawn=bool(getattr(resident, "self_repair_spawn", False)),
        self_repair_backend=str(getattr(resident, "self_repair_backend", "") or ""),
    )
    payload = {
        "command": command,
        "log_path": str(log_path),
        "state_dir": str(state_dir),
        "worktree_root": str(worktree_root),
        "output_root": str(output_root) if output_root is not None else "",
    }
    if dry_run:
        _append_event(event_log, "autoresearch.resident_sidecar.started", {
            **payload,
            "dry_run": True,
        })
        return None

    # State-dir scoped lock prevents duplicate resident loops when multiple
    # supervisors call `zf start` concurrently.
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.close()
        _append_event(event_log, "autoresearch.resident_sidecar.already_running", {
            **payload,
            "lock_path": str(lock_path),
        })
        return None

    log_handle = log_path.open("a", encoding="utf-8")
    env = os.environ.copy()
    env["ZF_AUTORESEARCH_RESIDENT"] = "authorized"
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(project_root),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            # Own process group: the resident spawns loop subprocesses which
            # spawn inner test runners (2h timeouts). `zf stop` runs in a
            # different process and can only clean the whole tree via
            # killpg on this group (R3/R4/R5: one orphaned inner runner per
            # round without this).
            start_new_session=True,
        )
    except Exception as exc:
        log_handle.close()
        _release_resident_lock(lock_handle)
        _append_event(event_log, "autoresearch.resident_sidecar.failed", {
            **payload,
            "error_type": type(exc).__name__,
            "error": str(exc)[:400],
        })
        return None

    pid_path.write_text(
        json.dumps(
            {
                "pid": process.pid,
                "command": command,
                "log_path": str(log_path),
                "started_at": time.time(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    sidecar = AutoresearchResidentSidecar(
        process=process,
        log_handle=log_handle,
        pid_path=pid_path,
        log_path=log_path,
        lock_handle=lock_handle,
    )
    time.sleep(0.25)
    exit_code = process.poll()
    if exit_code is not None:
        log_handle.close()
        _release_resident_lock(lock_handle)
        try:
            pid_path.unlink(missing_ok=True)
        except Exception:
            pass
        _append_event(event_log, "autoresearch.resident_sidecar.failed", {
            **payload,
            "pid": process.pid,
            "exit_code": exit_code,
            "reason": "exited_early",
        })
        return None
    _append_event(event_log, "autoresearch.resident_sidecar.started", {
        **payload,
        "pid": process.pid,
    })
    return sidecar


def stop_autoresearch_resident_sidecar(
    sidecar: AutoresearchResidentSidecar | None,
    *,
    event_log: Any | None = None,
    timeout: float = 10.0,
) -> None:
    if sidecar is None:
        return
    process = sidecar.process
    if process.poll() is None:
        pgid = _dedicated_sidecar_process_group(process.pid)
        if pgid is not None:
            _signal_process_group(pgid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if pgid is not None:
                _signal_process_group(pgid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=5.0)
        if pgid is not None and not _wait_for_process_group_exit(pgid, timeout):
            _signal_process_group(pgid, signal.SIGKILL)
            _wait_for_process_group_exit(pgid, 5.0)
    try:
        sidecar.log_handle.close()
    except Exception:
        pass
    _release_resident_lock(sidecar.lock_handle)
    _remove_resident_lock_if_free(sidecar.pid_path.parent)
    try:
        sidecar.pid_path.unlink(missing_ok=True)
    except Exception:
        pass
    _append_event(event_log, "autoresearch.resident_sidecar.stopped", {
        "pid": process.pid,
        "exit_code": process.returncode,
        "log_path": str(sidecar.log_path),
    })


def _dedicated_sidecar_process_group(pid: int) -> int | None:
    try:
        pgid = os.getpgid(pid)
    except (OSError, ProcessLookupError):
        return None
    return pgid if pgid == pid else None


def _signal_process_group(pgid: int, sig: signal.Signals) -> bool:
    try:
        os.killpg(pgid, sig)
    except (OSError, ProcessLookupError):
        return False
    return True


def _process_group_alive(pgid: int) -> bool:
    proc_root = Path("/proc")
    if proc_root.is_dir():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text(encoding="utf-8").rsplit(
                    ") ",
                    1,
                )[1].split()
                state = fields[0]
                process_group = int(fields[2])
            except (IndexError, OSError, ValueError):
                continue
            if process_group == pgid and state != "Z":
                return True
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_group_exit(pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(timeout, 0.0)
    while _process_group_alive(pgid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _pidfile_process_matches(pid: int, expected_command: list[str]) -> bool:
    """Guard against pid reuse: only treat the live process as ours when its
    cmdline still contains the distinctive tail of the recorded command."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
    except OSError:
        return False
    token = next(
        (part for part in reversed(expected_command) if str(part).strip()), "",
    )
    return bool(token) and str(token).encode() in cmdline


def stop_autoresearch_resident_sidecar_by_pidfile(
    state_dir: Path,
    *,
    event_log: Any | None = None,
    timeout: float = 10.0,
) -> bool:
    """Cross-process teardown for `zf stop`: the in-process handle variant
    above only serves the `zf start` owner. Reads the recorded pid, kills the
    sidecar's whole process group (loop subprocess + inner runners included —
    spawned with start_new_session), and clears the pid file. Returns True
    when a live sidecar was terminated."""
    pid_path = Path(state_dir) / "processes" / "autoresearch-resident.pid.json"
    try:
        record = json.loads(pid_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    pid = int(record.get("pid") or 0)
    command = [str(part) for part in (record.get("command") or [])]
    if pid <= 0 or not _pidfile_process_matches(pid, command):
        # Stale record (process gone or pid reused by something else): only
        # clear the file, never signal a stranger.
        pid_path.unlink(missing_ok=True)
        return False
    pgid = _dedicated_sidecar_process_group(pid)
    if pgid is not None:
        _signal_process_group(pgid, signal.SIGTERM)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pid_path.unlink(missing_ok=True)
            return False
    if pgid is not None:
        exited = _wait_for_process_group_exit(pgid, timeout)
    else:
        deadline = time.monotonic() + max(timeout, 0.0)
        while time.monotonic() < deadline:
            if not _pidfile_process_matches(pid, command):
                break
            time.sleep(0.05)
        exited = not _pidfile_process_matches(pid, command)
    if not exited:
        if pgid is not None:
            _signal_process_group(pgid, signal.SIGKILL)
            _wait_for_process_group_exit(pgid, 5.0)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    pid_path.unlink(missing_ok=True)
    _remove_resident_lock_if_free(pid_path.parent)
    _append_event(event_log, "autoresearch.resident_sidecar.stopped", {
        "pid": pid,
        "stopped_by": "pidfile",
        "log_path": str(record.get("log_path") or ""),
    })
    return True


def _release_resident_lock(lock_handle: Any | None) -> None:
    if lock_handle is None:
        return
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    except (OSError, ValueError):
        pass
    try:
        lock_handle.close()
    except Exception:
        pass


def _remove_resident_lock_if_free(processes_dir: Path) -> None:
    lock_path = Path(processes_dir) / "autoresearch-resident.lock"
    try:
        handle = lock_path.open("a+")
    except OSError:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass
    finally:
        _release_resident_lock(handle)


def _append_event(event_log: Any | None, event_type: str, payload: dict[str, Any]) -> None:
    if event_log is None:
        return
    try:
        event_log.append(ZfEvent(type=event_type, actor="zf-cli", payload=payload))
    except Exception:
        pass


__all__ = [
    "AutoresearchResidentSidecar",
    "build_autoresearch_resident_command",
    "start_autoresearch_resident_sidecar",
    "stop_autoresearch_resident_sidecar",
    "stop_autoresearch_resident_sidecar_by_pidfile",
]
