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
    bot_purpose: str = "default"
    app_id_label: str = ""


@dataclass
class FeishuInboundSidecarGroup:
    sidecars: list[FeishuInboundSidecar]


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
) -> FeishuInboundSidecar | FeishuInboundSidecarGroup | None:
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

    from zf.integrations.feishu.bot_credentials import inbound_bot_specs_for_config

    bot_specs = inbound_bot_specs_for_config(config)
    if not bot_specs:
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
    command = build_feishu_inbound_command(
        debounce_ms=debounce_ms,
        state_dir=state_dir.resolve(),
    )
    base_payload = {
        "command": command,
        "state_dir": str(state_dir),
    }
    started: list[FeishuInboundSidecar] = []
    for spec in bot_specs:
        suffix = _safe_suffix(spec.purpose)
        log_path = logs_dir / f"feishu-inbound-bridge-{suffix}.log"
        pid_path = processes_dir / f"feishu-inbound-bridge-{suffix}.pid.json"
        payload = {
            **base_payload,
            "bot_purpose": spec.purpose,
            "app_id_label": spec.credential.app_label,
            "app_id_env": spec.credential.app_id_env,
            "fallback_credentials": spec.credential.fallback,
            "route_count": spec.route_count,
            "log_path": str(log_path),
        }
        if dry_run:
            _append_event(event_log, "feishu.inbound_bridge.started", {
                **payload,
                "dry_run": True,
            })
            continue

        log_handle = log_path.open("a", encoding="utf-8")
        env = os.environ.copy()
        env["FEISHU_APP_ID"] = spec.credential.app_id
        env["FEISHU_APP_SECRET"] = spec.credential.app_secret
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
            _append_event(event_log, "feishu.inbound_bridge.failed", {
                **payload,
                "error_type": type(exc).__name__,
                "error": str(exc)[:400],
            })
            continue

        pid_path.write_text(
            json.dumps(
                {
                    "pid": process.pid,
                    "command": command,
                    "bot_purpose": spec.purpose,
                    "app_id_label": spec.credential.app_label,
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
            bot_purpose=spec.purpose,
            app_id_label=spec.credential.app_label,
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
            continue
        _append_event(event_log, "feishu.inbound_bridge.started", {
            **payload,
            "pid": process.pid,
        })
        started.append(sidecar)

    if not started:
        return None
    if len(started) == 1:
        return started[0]
    return FeishuInboundSidecarGroup(started)


def stop_feishu_inbound_sidecar(
    sidecar: FeishuInboundSidecar | FeishuInboundSidecarGroup | None,
    *,
    event_log: Any | None = None,
    timeout: float = 10.0,
) -> None:
    if sidecar is None:
        return
    if isinstance(sidecar, FeishuInboundSidecarGroup):
        for item in sidecar.sidecars:
            stop_feishu_inbound_sidecar(item, event_log=event_log, timeout=timeout)
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
        "bot_purpose": sidecar.bot_purpose,
        "app_id_label": sidecar.app_id_label,
    })


def _safe_suffix(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (value or "default"))
    return safe.strip("-_") or "default"


def _append_event(event_log: Any | None, event_type: str, payload: dict[str, Any]) -> None:
    if event_log is None:
        return
    try:
        event_log.append(ZfEvent(type=event_type, actor="zf-cli", payload=payload))
    except Exception:
        pass
