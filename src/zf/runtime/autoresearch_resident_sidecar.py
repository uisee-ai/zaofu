"""Managed Autoresearch resident sidecar for ``zf start``.

The sidecar owns only process lifecycle. Loop semantics stay in
``zf autoresearch resident`` and the existing Autoresearch event contracts.
"""

from __future__ import annotations

import json
import os
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
        )
    except Exception as exc:
        log_handle.close()
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
    )
    time.sleep(0.25)
    exit_code = process.poll()
    if exit_code is not None:
        log_handle.close()
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
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
    try:
        sidecar.log_handle.close()
    except Exception:
        pass
    try:
        sidecar.pid_path.unlink(missing_ok=True)
    except Exception:
        pass
    _append_event(event_log, "autoresearch.resident_sidecar.stopped", {
        "pid": process.pid,
        "exit_code": process.returncode,
        "log_path": str(sidecar.log_path),
    })


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
]
