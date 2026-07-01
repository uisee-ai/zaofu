"""Managed Feishu inbound sidecar for ``zf start``.

The sidecar owns only process lifecycle. Message semantics stay in
``zf feishu bridge --watch`` and the existing Feishu routing layer.
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
class FeishuInboundSidecar:
    process: subprocess.Popen
    log_handle: Any
    pid_path: Path
    log_path: Path


def build_feishu_inbound_command(*, debounce_ms: int, state_dir: Path) -> list[str]:
    return [
        *shlex.split(zf_cli_cmd()),
        "feishu",
        "bridge",
        "--watch",
        "--debounce-ms",
        str(debounce_ms),
        "--state-dir",
        str(state_dir),
    ]


def start_feishu_inbound_sidecar(
    *,
    config: object,
    state_dir: Path,
    project_root: Path,
    event_log: Any | None = None,
    dry_run: bool = False,
) -> FeishuInboundSidecar | None:
    runtime = getattr(config, "runtime", None)
    inbound = getattr(runtime, "feishu_inbound", None)
    if not inbound or not bool(getattr(inbound, "enabled", False)):
        return None

    mode = str(getattr(inbound, "mode", "bridge") or "bridge")
    if mode != "bridge":
        _append_event(
            event_log,
            "feishu.inbound_bridge.skipped",
            {"reason": "unsupported_mode", "mode": mode},
        )
        return None

    integrations = getattr(config, "integrations", None)
    routing = getattr(integrations, "feishu_routing", None)
    require_routing = bool(getattr(inbound, "require_routing", True))
    if require_routing and not routing:
        _append_event(
            event_log,
            "feishu.inbound_bridge.skipped",
            {"reason": "missing_feishu_routing"},
        )
        return None

    if not _has_feishu_credentials():
        _append_event(
            event_log,
            "feishu.inbound_bridge.skipped",
            {"reason": "missing_credentials"},
        )
        return None

    try:
        debounce_ms = int(getattr(inbound, "debounce_ms", 600))
    except (TypeError, ValueError):
        debounce_ms = 600
    logs_dir = state_dir / "logs"
    processes_dir = state_dir / "processes"
    logs_dir.mkdir(parents=True, exist_ok=True)
    processes_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "feishu-inbound-bridge.log"
    pid_path = processes_dir / "feishu-inbound-bridge.pid.json"
    command = build_feishu_inbound_command(
        debounce_ms=debounce_ms,
        state_dir=state_dir.resolve(),
    )
    payload = {
        "command": command,
        "log_path": str(log_path),
        "state_dir": str(state_dir),
    }
    if dry_run:
        _append_event(event_log, "feishu.inbound_bridge.started", {
            **payload,
            "dry_run": True,
        })
        return None

    log_handle = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(project_root),
            env=os.environ.copy(),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception as exc:
        log_handle.close()
        _append_event(event_log, "feishu.inbound_bridge.failed", {
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
    sidecar = FeishuInboundSidecar(
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
        _append_event(event_log, "feishu.inbound_bridge.failed", {
            **payload,
            "pid": process.pid,
            "exit_code": exit_code,
            "reason": "exited_early",
        })
        return None
    _append_event(event_log, "feishu.inbound_bridge.started", {
        **payload,
        "pid": process.pid,
    })
    return sidecar


def stop_feishu_inbound_sidecar(
    sidecar: FeishuInboundSidecar | None,
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
    _append_event(event_log, "feishu.inbound_bridge.stopped", {
        "pid": process.pid,
        "exit_code": process.returncode,
        "log_path": str(sidecar.log_path),
    })


def _has_feishu_credentials() -> bool:
    app_id = os.environ.get("FEISHU_APP_ID", "") or os.environ.get(
        "LARKSUITE_CLI_APP_ID", ""
    )
    app_secret = os.environ.get("FEISHU_APP_SECRET", "") or os.environ.get(
        "LARKSUITE_CLI_APP_SECRET", ""
    )
    return bool(app_id and app_secret)


def _append_event(event_log: Any | None, event_type: str, payload: dict[str, Any]) -> None:
    if event_log is None:
        return
    try:
        event_log.append(ZfEvent(type=event_type, actor="zf-cli", payload=payload))
    except Exception:
        pass
