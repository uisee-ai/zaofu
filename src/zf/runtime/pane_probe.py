"""Read-only tmux pane probe for Supervisor runtime observation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from zf.core.config.schema import ZfConfig
from zf.core.security.redaction import redact_obj
from zf.runtime.tmux import TmuxSession


PANE_PROBE_SCHEMA_VERSION = "runtime.pane_probe.v0"
Runner = Callable[..., subprocess.CompletedProcess[str]]

# 交互确认假死态标记(avbs-r5 实证:9/15 codex pane 停在撞限确认提示,
# pane 活/进程活/事件静默,heartbeat/stuck/respawn/drift 全部失明)。
# 这是一个类别——任何 TUI 等键盘输入的场景都构成假死;标记只匹配 pane
# 尾部 excerpt(等待提示必然停在底部),避免误伤滚屏历史。
_INTERACTIVE_PROMPT_MARKERS: tuple[tuple[str, str], ...] = (
    ("usage_limit_reached", "hit your usage limit"),
    ("login_required", "please sign in"),
    ("login_required", "session expired"),
    ("login_required", "please log in"),
    ("trust_prompt", "do you trust the files"),
)
_FLEET_CORRELATED_MIN = 3


def _interactive_prompt_marker(excerpt: str) -> str:
    text = (excerpt or "").lower()
    for key, needle in _INTERACTIVE_PROMPT_MARKERS:
        if needle in text:
            return key
    return ""


def build_runtime_pane_probe(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    now: datetime | None = None,
    runner: Runner | None = None,
    capture_lines: int = 80,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if config is None:
        return _disabled("config_missing", state_dir=state_dir, now=current)
    session_name = getattr(config.session, "tmux_session", "") or "zf"
    layout = getattr(config.session, "tmux_layout", "") or "window_per_role"
    roles = _tmux_roles(config)
    bindings = _read_bindings(Path(state_dir))
    role_meta = _role_session_meta(Path(state_dir))
    run = runner or subprocess.run

    panes: list[dict[str, Any]] = []
    for role in roles:
        instance_id = role["instance_id"]
        target_ref = _target_for_role(
            instance_id=instance_id,
            session_name=str(role.get("session_name") or session_name),
            layout=str(role.get("layout") or layout),
            bindings=bindings,
            source=str(role.get("target_source") or ""),
        )
        panes.append(_probe_role(
            instance_id=instance_id,
            role_name=role["role"],
            backend=role["backend"],
            target_ref=target_ref,
            state_dir=Path(state_dir),
            project_root=project_root,
            role_meta=role_meta.get(instance_id) or {},
            role_threshold=float(role.get("stuck_threshold_seconds") or 300.0),
            now=current,
            runner=run,
            capture_lines=max(10, min(capture_lines, 300)),
        ))
    status_counts = Counter(str(item.get("activity_status") or "unknown") for item in panes)
    marker_counts = Counter(
        str(item.get("interactive_prompt_marker") or "")
        for item in panes
        if item.get("interactive_prompt_marker")
    )
    correlated = {
        marker: count for marker, count in marker_counts.items()
        if count >= _FLEET_CORRELATED_MIN
    }
    return redact_obj({
        "schema_version": PANE_PROBE_SCHEMA_VERSION,
        "is_derived_projection": True,
        "enabled": True,
        "generated_at": current.isoformat(),
        "state_dir": str(state_dir),
        "project_root": str(project_root or ""),
        "session_name": session_name,
        "layout": layout,
        "summary": {
            "expected": len(roles),
            "observed": sum(1 for item in panes if item.get("alive")),
            "mismatch": status_counts.get("activity_mismatch", 0),
            "missing": status_counts.get("pane_missing", 0) + status_counts.get("missing_binding", 0),
            "interactive_prompt": status_counts.get("interactive_prompt", 0),
            "correlated_interactive_prompts": correlated,
            "by_status": dict(sorted(status_counts.items())),
        },
        "panes": panes,
    })


def pane_probe_attention_items(probe: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert pane probe mismatches into Supervisor attention candidates."""

    items: list[dict[str, Any]] = []
    if not probe.get("enabled"):
        return items
    correlated = (
        (probe.get("summary") or {}).get("correlated_interactive_prompts") or {}
    )
    for marker, count in sorted(correlated.items()):
        # 舰队级相关性失效(共享账号配额 → 多 worker 同时死):比单
        # pane 更高一级,critical + 人类必需。
        fingerprint = f"pane_probe_fleet:{marker}"
        items.append(redact_obj({
            "schema_version": "attention-item.v0",
            "attention_id": "attn-" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12],
            "source": "pane_probe",
            "fingerprint": fingerprint,
            "severity": "critical",
            "status": "open",
            "human_action_required": True,
            "title": f"{count} workers stalled at the same interactive prompt ({marker})",
            "summary": (
                "Multiple worker panes are waiting at the same interactive "
                "confirmation prompt (shared quota/login). No event-based "
                "detector can see this; a human keystroke or quota action is "
                "required to resume the fleet."
            ),
            "task_id": "",
            "source_event_ids": [],
            "source_ref": "",
            "suggested_route": "owner_notify",
            "suggested_action": {
                "kind": "confirm_interactive_prompt_fleet",
                "marker": marker,
                "count": count,
            },
        }))
    for pane in probe.get("panes") or []:
        if not isinstance(pane, dict):
            continue
        status = str(pane.get("activity_status") or "")
        if status == "interactive_prompt":
            instance_id = str(pane.get("instance_id") or "")
            marker = str(pane.get("interactive_prompt_marker") or "")
            fingerprint = f"pane_probe_interactive:{instance_id}:{marker}"
            items.append(redact_obj({
                "schema_version": "attention-item.v0",
                "attention_id": "attn-" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12],
                "source": "pane_probe",
                "fingerprint": fingerprint,
                "severity": "high",
                "status": "open",
                "human_action_required": True,
                "title": f"{instance_id} waiting at interactive prompt ({marker})",
                "summary": (
                    "Worker pane is alive but parked at an interactive "
                    "confirmation prompt (usage limit / login / trust). It "
                    "will not recover on its own and emits no events; a "
                    "human keystroke is required."
                ),
                "task_id": str(pane.get("current_task_id") or ""),
                "source_event_ids": [],
                "source_ref": str(pane.get("target") or ""),
                "suggested_route": "owner_notify",
                "suggested_action": {
                    "kind": "confirm_interactive_prompt",
                    "instance_id": instance_id,
                    "marker": marker,
                    "pane": str(pane.get("pane") or ""),
                },
            }))
            continue
        if status != "activity_mismatch":
            continue
        instance_id = str(pane.get("instance_id") or "")
        task_id = str(pane.get("current_task_id") or "")
        fingerprint = f"pane_probe:{instance_id}:{task_id or status}"
        attention_id = "attn-" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]
        items.append(redact_obj({
            "schema_version": "attention-item.v0",
            "attention_id": attention_id,
            "source": "pane_probe",
            "fingerprint": fingerprint,
            "severity": "medium",
            "status": "open",
            "title": f"{instance_id} pane active but heartbeat is stale",
            "summary": (
                "tmux pane still has visible output, but role heartbeat is "
                "past the stuck threshold. Treat this as an observation gap "
                "first; promote only if paired with a terminal failure or "
                "repeated missing events."
            ),
            "task_id": task_id,
            "source_event_ids": [],
            "source_ref": str(pane.get("target") or ""),
            "suggested_route": "owner_notify",
            "suggested_action": {
                "kind": "observe_runtime_liveness_gap",
                "instance_id": instance_id,
                "pane": str(pane.get("pane") or ""),
                "current_command": str(pane.get("current_command") or ""),
                "output_sha256": str(pane.get("output_sha256") or ""),
            },
        }))
    return items


def _disabled(reason: str, *, state_dir: Path, now: datetime) -> dict[str, Any]:
    return {
        "schema_version": PANE_PROBE_SCHEMA_VERSION,
        "is_derived_projection": True,
        "enabled": False,
        "reason": reason,
        "generated_at": now.isoformat(),
        "state_dir": str(state_dir),
        "summary": {"expected": 0, "observed": 0, "mismatch": 0, "missing": 0, "by_status": {}},
        "panes": [],
    }


def _tmux_roles(config: ZfConfig) -> list[dict[str, Any]]:
    roles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for role in getattr(config, "roles", []) or []:
        if getattr(role, "transport", "tmux") != "tmux":
            continue
        instance_id = getattr(role, "instance_id", "") or getattr(role, "name", "")
        if not instance_id:
            continue
        seen.add(str(instance_id))
        roles.append({
            "role": getattr(role, "name", "") or instance_id,
            "instance_id": instance_id,
            "backend": getattr(role, "backend", "") or "",
            "stuck_threshold_seconds": getattr(role, "stuck_threshold_seconds", 300.0),
        })
    try:
        from zf.runtime.run_manager_resident import (
            build_resident_run_manager_role,
            resident_run_manager_session_mode,
            resident_run_manager_tmux_session,
        )

        resident = build_resident_run_manager_role(config)
    except Exception:
        resident = None
    if resident is not None:
        instance_id = getattr(resident, "instance_id", "") or getattr(resident, "name", "")
        if instance_id and instance_id not in seen and getattr(resident, "transport", "tmux") == "tmux":
            session_mode = resident_run_manager_session_mode(config)
            roles.append({
                "role": getattr(resident, "name", "") or instance_id,
                "instance_id": instance_id,
                "backend": getattr(resident, "backend", "") or "",
                "stuck_threshold_seconds": getattr(resident, "stuck_threshold_seconds", 300.0),
                "session_name": resident_run_manager_tmux_session(config),
                "layout": "window_per_role" if session_mode == "dedicated" else "",
                "target_source": (
                    "run_manager_resident"
                    if session_mode == "dedicated"
                    else ""
                ),
            })
    return roles


def _target_for_role(
    *,
    instance_id: str,
    session_name: str,
    layout: str,
    bindings: dict[str, Any],
    source: str = "",
) -> dict[str, str]:
    if layout == "pane_grid":
        roles = bindings.get("roles") if isinstance(bindings.get("roles"), dict) else {}
        entry = roles.get(instance_id) if isinstance(roles, dict) else None
        if isinstance(entry, dict):
            pane = str(entry.get("pane") or entry.get("pane_id") or "").strip()
            if pane:
                return {
                    "target": pane,
                    "session": str(entry.get("session") or session_name),
                    "window": str(entry.get("window") or "roles"),
                    "pane": pane,
                    "source": "pane_bindings.json",
                }
        return {
            "target": "",
            "session": session_name,
            "window": str(bindings.get("window") or "roles"),
            "pane": "",
            "source": "missing_binding",
        }
    return {
        "target": f"{session_name}:{instance_id}",
        "session": session_name,
        "window": instance_id,
        "pane": "",
        "source": source or "zf.yaml",
    }


def _probe_role(
    *,
    instance_id: str,
    role_name: str,
    backend: str,
    target_ref: dict[str, str],
    state_dir: Path,
    project_root: Path | None,
    role_meta: dict[str, Any],
    role_threshold: float,
    now: datetime,
    runner: Runner,
    capture_lines: int,
) -> dict[str, Any]:
    target = str(target_ref.get("target") or "")
    heartbeat = role_meta.get("last_heartbeat_payload")
    heartbeat_payload = heartbeat if isinstance(heartbeat, dict) else {}
    last_heartbeat_at = str(role_meta.get("last_heartbeat_at") or heartbeat_payload.get("ts") or "")
    heartbeat_age = _age_seconds(last_heartbeat_at, now)
    base = {
        "instance_id": instance_id,
        "role": role_name,
        "backend": backend,
        "target": target,
        "target_source": str(target_ref.get("source") or ""),
        "session": str(target_ref.get("session") or ""),
        "window": str(target_ref.get("window") or ""),
        "pane": str(target_ref.get("pane") or ""),
        "current_task_id": str(heartbeat_payload.get("current_task_id") or ""),
        "heartbeat_state": str(heartbeat_payload.get("state") or role_meta.get("state") or ""),
        "last_heartbeat_at": last_heartbeat_at,
        "last_heartbeat_age_sec": heartbeat_age,
        "stuck_threshold_seconds": role_threshold,
        "alive": False,
        "activity_status": "unknown",
        "capture_ok": False,
        "output_sha256": "",
        "excerpt": "",
        "current_command": "",
        "current_path": "",
    }
    if not target:
        return {**base, "activity_status": "missing_binding"}
    display = _tmux(
        runner,
        [
            "tmux", "display-message",
            "-p", "-t", target,
            "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}\t#{pane_dead}",
        ],
    )
    if display.returncode != 0:
        return {**base, "activity_status": "pane_missing", "error": _short_error(display)}
    pane_id, current_command, current_path, pane_dead = _split_display(display.stdout)
    capture = _tmux(
        runner,
        ["tmux", "capture-pane", "-t", target, "-p", "-S", str(-capture_lines)],
    )
    excerpt = ""
    digest = ""
    if capture.returncode == 0:
        text = TmuxSession.strip_ansi(capture.stdout or "")
        excerpt = _excerpt(text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
    alive = pane_dead != "1"
    prompt_marker = _interactive_prompt_marker(excerpt) if alive else ""
    status = _activity_status(
        alive=alive,
        capture_ok=capture.returncode == 0,
        output_digest=digest,
        heartbeat_age=heartbeat_age,
        threshold=role_threshold,
        heartbeat_state=str(base["heartbeat_state"]),
    )
    if prompt_marker:
        # 交互确认假死优先于其它判定:pane 停在等键盘输入的提示上,
        # 事件沉默与否都不会自行恢复。
        status = "interactive_prompt"
    return redact_obj({
        **base,
        "alive": alive,
        "interactive_prompt_marker": prompt_marker,
        "activity_status": status,
        "capture_ok": capture.returncode == 0,
        "pane": pane_id or str(base["pane"]),
        "current_command": current_command,
        "current_path": _display_path(current_path, state_dir=state_dir, project_root=project_root),
        "output_sha256": digest,
        "excerpt": excerpt,
        "error": "" if capture.returncode == 0 else _short_error(capture),
    })


def _tmux(runner: Runner, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(
            args,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        return subprocess.CompletedProcess(args, 127, stdout="", stderr=str(exc))
    returncode = getattr(result, "returncode", 127)
    stdout = getattr(result, "stdout", "")
    stderr = getattr(result, "stderr", "")
    if not isinstance(returncode, int):
        returncode = 127
    if not isinstance(stdout, str):
        stdout = ""
    if not isinstance(stderr, str):
        stderr = ""
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def _activity_status(
    *,
    alive: bool,
    capture_ok: bool,
    output_digest: str,
    heartbeat_age: int | None,
    threshold: float,
    heartbeat_state: str,
) -> str:
    if not alive:
        return "pane_missing"
    if not capture_ok:
        return "capture_failed"
    if (
        output_digest
        and heartbeat_age is not None
        and heartbeat_age > max(threshold, 1.0)
        and heartbeat_state in {"busy", "in_progress", "running", "unknown", ""}
    ):
        return "activity_mismatch"
    return "observed"


def _split_display(value: str) -> tuple[str, str, str, str]:
    parts = (value or "").strip().split("\t", 3)
    while len(parts) < 4:
        parts.append("")
    return parts[0], parts[1], parts[2], parts[3]


def _excerpt(text: str, *, max_chars: int = 600) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    value = "\n".join(lines[-12:])
    if len(value) > max_chars:
        value = value[-max_chars:]
    return value


def _short_error(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stderr or proc.stdout or "").strip()[:240]


def _read_bindings(state_dir: Path) -> dict[str, Any]:
    path = Path(state_dir) / "pane_bindings.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _role_session_meta(state_dir: Path) -> dict[str, dict[str, Any]]:
    path = Path(state_dir) / "role_sessions.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    meta = data.get("instance_meta") if isinstance(data, dict) else {}
    if not isinstance(meta, dict):
        return {}
    return {str(key): dict(value) if isinstance(value, dict) else {} for key, value in meta.items()}


def _age_seconds(value: str, now: datetime) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds()))


def _display_path(path: str, *, state_dir: Path, project_root: Path | None) -> str:
    if not path:
        return ""
    raw = Path(path)
    for parent in (project_root, state_dir):
        if parent is None:
            continue
        try:
            return str(raw.resolve(strict=False).relative_to(Path(parent).resolve(strict=False)))
        except ValueError:
            continue
    return path


__all__ = [
    "PANE_PROBE_SCHEMA_VERSION",
    "build_runtime_pane_probe",
    "pane_probe_attention_items",
]
