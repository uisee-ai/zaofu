"""Managed Feishu inbound sidecar for ``zf start``.

The sidecar owns only process lifecycle. Message semantics stay in
``zf feishu bridge --watch`` and the existing Feishu routing layer.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.integrations.feishu.workspace_bridge_lease import (
    ProviderBridgeLease,
    join_live_provider_bridge,
    register_provider_bridge,
    release_provider_bridge,
)
from zf.runtime.cli_command import zf_cli_cmd


@dataclass
class FeishuInboundSidecar:
    process: subprocess.Popen | None
    log_handle: Any | None
    pid_path: Path | None
    log_path: Path
    bot_purpose: str = "default"
    app_id_label: str = ""
    provider_lease: ProviderBridgeLease | None = None


@dataclass
class FeishuInboundSidecarGroup:
    sidecars: list[FeishuInboundSidecar]


def build_feishu_inbound_command(
    *,
    debounce_ms: int,
    state_dir: Path,
    workspace: str = "",
    app_id: str = "",
    all_workspaces: bool = False,
    issue_merge: bool = False,
) -> list[str]:
    command = [
        *shlex.split(zf_cli_cmd()),
        "feishu",
        "bridge",
        "--watch",
        "--debounce-ms",
        str(debounce_ms),
        "--state-dir",
        str(state_dir),
    ]
    if workspace:
        command.extend(["--workspace", workspace])
    if all_workspaces:
        command.append("--all-workspaces")
    if issue_merge:
        command.append("--issue-merge")
    if app_id:
        command.extend(["--app-id", app_id])
    return command


def _start_bridge_process(command: list[str], **kwargs: Any) -> subprocess.Popen:
    return subprocess.Popen(command, **kwargs)


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
    issue = getattr(integrations, "feishu_issue_merge", None)
    issue_only = bool(getattr(issue, "enabled", False))
    routing = getattr(integrations, "feishu_routing", None)
    from zf.integrations.feishu.project_group_binding import (
        ProjectFeishuGroupBindingStore,
        configured_project_group,
    )

    group_config = configured_project_group(config)
    group_binding = (
        ProjectFeishuGroupBindingStore(state_dir).get(
            str(getattr(group_config, "binding_id", ""))
        )
        if group_config is not None
        else None
    )
    group_mode = group_binding is not None and group_binding.status == "active"
    if group_config is not None and not group_mode:
        _append_event(
            event_log,
            "feishu.inbound_bridge.skipped",
            {
                "reason": "missing_active_project_group_binding",
                "binding_id": str(getattr(group_config, "binding_id", "")),
                "status": str(getattr(group_binding, "status", "missing")),
            },
        )
        return None
    require_routing = bool(getattr(inbound, "require_routing", True))
    if require_routing and not routing and not group_mode and not issue_only:
        _append_event(
            event_log,
            "feishu.inbound_bridge.skipped",
            {"reason": "missing_feishu_routing"},
        )
        return None

    from zf.integrations.feishu.bot_credentials import (
        FeishuBotCredential,
        FeishuInboundBotSpec,
        credential_for_purpose,
        inbound_bot_specs_for_config,
    )

    if issue_only:
        bot_specs = [FeishuInboundBotSpec(
            purpose="issue_merge",
            credential=FeishuBotCredential(
                purpose="issue_merge", app_id=str(issue.app_id),
                app_secret=os.environ.get("FEISHU_ISSUE_MERGE_APP_SECRET", ""),
                app_id_env="FEISHU_ISSUE_MERGE_APP_ID",
                app_secret_env="FEISHU_ISSUE_MERGE_APP_SECRET",
            ), route_count=1,
        )]
    elif group_mode:
        assert group_binding is not None
        bot_specs = []
        for bot in group_binding.bots:
            if bot.membership_status != "active":
                continue
            credential = credential_for_purpose(
                bot.purpose,
                allow_fallback=False,
            )
            if credential is None or credential.app_id != bot.app_id:
                continue
            bot_specs.append(
                FeishuInboundBotSpec(
                    purpose=bot.purpose,
                    credential=credential,
                    route_count=1,
                )
            )
    else:
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
    started: list[FeishuInboundSidecar] = []
    for spec in bot_specs:
        command = build_feishu_inbound_command(
            debounce_ms=debounce_ms,
            state_dir=state_dir.resolve(),
            workspace="",
            app_id=(spec.credential.app_id if group_mode else ""),
            all_workspaces=group_mode,
            issue_merge=issue_only,
        )
        base_payload = {
            "command": command,
            "state_dir": str(state_dir),
        }
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
            "workspace_id": (
                group_binding.workspace_id if group_mode and group_binding else ""
            ),
            "project_group_binding_id": (
                group_binding.binding_id if group_mode and group_binding else ""
            ),
            "log_path": str(log_path),
        }
        if dry_run:
            _append_event(event_log, "feishu.inbound_bridge.started", {
                **payload,
                "dry_run": True,
            })
            continue

        if group_mode and group_binding is not None:
            shared_lease = join_live_provider_bridge(
                workspace_id=group_binding.workspace_id,
                app_id=spec.credential.app_id,
                project_id=group_binding.project_id,
            )
            if shared_lease is not None:
                started.append(_shared_provider_sidecar(
                    lease=shared_lease,
                    log_path=log_path,
                    bot_purpose=spec.purpose,
                    app_id_label=spec.credential.app_label,
                ))
                _append_event(event_log, "feishu.inbound_bridge.shared", {
                    **payload,
                    "pid": shared_lease.pid,
                    "shared_with_existing_project": True,
                })
                continue

        log_handle = log_path.open("a", encoding="utf-8")
        env = os.environ.copy()
        env["FEISHU_APP_ID"] = spec.credential.app_id
        env["FEISHU_APP_SECRET"] = spec.credential.app_secret
        try:
            process = _start_bridge_process(
                command,
                cwd=str(project_root),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
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
            shared_lease = (
                join_live_provider_bridge(
                    workspace_id=group_binding.workspace_id,
                    app_id=spec.credential.app_id,
                    project_id=group_binding.project_id,
                )
                if group_mode and group_binding is not None
                else None
            )
            if shared_lease is not None:
                started.append(_shared_provider_sidecar(
                    lease=shared_lease,
                    log_path=log_path,
                    bot_purpose=spec.purpose,
                    app_id_label=spec.credential.app_label,
                ))
                _append_event(event_log, "feishu.inbound_bridge.shared", {
                    **payload,
                    "pid": shared_lease.pid,
                    "shared_with_existing_project": True,
                    "reason": "concurrent_provider_bridge_won",
                })
                continue
            _append_event(event_log, "feishu.inbound_bridge.failed", {
                **payload,
                "pid": process.pid,
                "exit_code": exit_code,
                "reason": "exited_early",
            })
            continue
        if group_mode and group_binding is not None:
            lease = register_provider_bridge(
                workspace_id=group_binding.workspace_id,
                app_id=spec.credential.app_id,
                project_id=group_binding.project_id,
                pid=process.pid,
                log_path=log_path,
            )
            if lease.shared:
                _terminate_process(process)
                log_handle.close()
                pid_path.unlink(missing_ok=True)
                started.append(_shared_provider_sidecar(
                    lease=lease,
                    log_path=log_path,
                    bot_purpose=spec.purpose,
                    app_id_label=spec.credential.app_label,
                ))
                _append_event(event_log, "feishu.inbound_bridge.shared", {
                    **payload,
                    "pid": lease.pid,
                    "shared_with_existing_project": True,
                    "reason": "provider_bridge_registration_race",
                })
                continue
            sidecar.provider_lease = lease
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
    if sidecar.provider_lease is not None:
        _stop_provider_sidecar(sidecar, event_log=event_log, timeout=timeout)
        return
    process = sidecar.process
    assert process is not None
    _terminate_process(process, timeout=timeout)
    try:
        if sidecar.log_handle is not None:
            sidecar.log_handle.close()
    except Exception:
        pass
    try:
        if sidecar.pid_path is not None:
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


def _shared_provider_sidecar(
    *,
    lease: ProviderBridgeLease,
    log_path: Path,
    bot_purpose: str,
    app_id_label: str,
) -> FeishuInboundSidecar:
    return FeishuInboundSidecar(
        process=None,
        log_handle=None,
        pid_path=None,
        log_path=Path(lease.log_path) if lease.log_path else log_path,
        bot_purpose=bot_purpose,
        app_id_label=app_id_label,
        provider_lease=lease,
    )


def _stop_provider_sidecar(
    sidecar: FeishuInboundSidecar,
    *,
    event_log: Any | None,
    timeout: float,
) -> None:
    lease = sidecar.provider_lease
    assert lease is not None
    release = release_provider_bridge(lease)
    if release.terminate:
        process = sidecar.process
        if process is not None and process.pid == release.pid:
            _terminate_process(process, timeout=timeout)
        else:
            _terminate_external_process(release.pid, timeout=timeout)
    try:
        if sidecar.log_handle is not None:
            sidecar.log_handle.close()
    except Exception:
        pass
    try:
        if sidecar.pid_path is not None:
            sidecar.pid_path.unlink(missing_ok=True)
    except Exception:
        pass
    _append_event(
        event_log,
        "feishu.inbound_bridge.stopped"
        if release.terminate
        else "feishu.inbound_bridge.detached",
        {
            "pid": release.pid,
            "log_path": str(sidecar.log_path),
            "bot_purpose": sidecar.bot_purpose,
            "app_id_label": sidecar.app_id_label,
            "workspace_id": lease.workspace_id,
            "project_id": lease.project_id,
            "remaining_projects": list(release.remaining_projects),
        },
    )


def _terminate_process(process: subprocess.Popen, *, timeout: float = 10.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)


def _terminate_external_process(pid: int, *, timeout: float) -> None:
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


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
