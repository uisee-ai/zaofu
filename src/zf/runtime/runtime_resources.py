"""Read-only runtime resource and provider session projections."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from zf.core.events.factory import event_log_from_project
from zf.core.security.redaction import redact_obj, redact_text


RUNTIME_RESOURCE_SCHEMA_VERSION = "runtime-resources.v1"


def build_runtime_resource_projection(
    state_dir: Path,
    *,
    project_root: Path | None = None,
    config: Any | None = None,
    events: Iterable[Any] | None = None,
    max_excerpt_bytes: int = 2048,
    max_terminal_sessions: int = 6,
    tmux_output: str | None = None,
) -> dict[str, Any]:
    """Project runtime resources without mutating state or provider sessions."""

    project_root = project_root or state_dir.parent
    event_rows = list(events) if events is not None else _read_events(state_dir, config)
    role_data = _read_yaml(state_dir / "role_sessions.yaml")
    session_data = _read_yaml(state_dir / "session.yaml")
    roles = _string_map(role_data.get("roles"))
    meta = _dict_map(role_data.get("instance_meta"))
    worker_states = _latest_worker_states(event_rows)
    active_tasks = _latest_active_tasks(meta, event_rows)
    provider_sessions: list[dict[str, Any]] = []
    terminal_excerpts: list[dict[str, Any]] = []

    for instance_id in sorted(set(roles) | set(meta)):
        item_meta = meta.get(instance_id, {})
        session_id = roles.get(instance_id, "")
        path = _first_session_path(item_meta)
        session_ref = _session_ref(path, project_root=project_root)
        heartbeat = item_meta.get("last_heartbeat_payload")
        if not isinstance(heartbeat, dict):
            heartbeat = {}
        provider_sessions.append({
            "instance_id": instance_id,
            "role": _parent_role(instance_id),
            "backend": str(item_meta.get("backend") or ""),
            "session_id": session_id,
            "state": worker_states.get(instance_id, str(heartbeat.get("state") or "unknown")),
            "task_id": active_tasks.get(instance_id, ""),
            "spawned_at": str(item_meta.get("spawned_at") or ""),
            "last_heartbeat_at": str(item_meta.get("last_heartbeat_at") or ""),
            "stale": _heartbeat_stale(str(item_meta.get("last_heartbeat_at") or "")),
            "provider_pid": _first_int(item_meta, ("provider_pid", "pid", "process_id")),
            "session_ref": session_ref,
            "resume_ready": bool(session_id or session_ref.get("exists")),
        })
        if path is not None and len(terminal_excerpts) < max_terminal_sessions:
            terminal_excerpts.append(
                _terminal_excerpt(
                    instance_id=instance_id,
                    backend=str(item_meta.get("backend") or ""),
                    path=path,
                    project_root=project_root,
                    max_bytes=max_excerpt_bytes,
                )
            )

    host = {
        "host_id": "host-0",
        "tmux": _tmux_projection(config=config, meta=meta, tmux_output=tmux_output),
    }
    projection = {
        "schema_version": RUNTIME_RESOURCE_SCHEMA_VERSION,
        "is_derived_projection": True,
        "truth_sources": ["session.yaml", "role_sessions.yaml", "events.jsonl"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "provider_sessions": len(provider_sessions),
            "terminal_excerpts": len(terminal_excerpts),
            "stale_sessions": sum(1 for item in provider_sessions if item["stale"]),
            "tmux_sessions": len(host["tmux"].get("sessions", [])),
        },
        "session": redact_obj(session_data),
        "provider_sessions": provider_sessions,
        "terminal_excerpts": terminal_excerpts,
        "host": host,
        "policy": {
            "mode": "read-only",
            "terminal_excerpt": "tail-window-redacted",
            "mutating_commands": False,
            "raw_secret_persistence": False,
        },
    }
    return redact_obj(projection)


def _read_events(state_dir: Path, config: Any | None) -> list[Any]:
    try:
        return list(event_log_from_project(state_dir, config=config).read_all())
    except Exception:
        return []


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(raw) for key, raw in value.items()}


def _dict_map(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): dict(raw) if isinstance(raw, dict) else {}
        for key, raw in value.items()
    }


def _latest_worker_states(events: Iterable[Any]) -> dict[str, str]:
    states: dict[str, str] = {}
    for event in events:
        if _etype(event) != "worker.state.changed":
            continue
        payload = _payload(event)
        actor = _actor(event)
        if actor and payload.get("to"):
            states[actor] = str(payload["to"])
    return states


def _latest_active_tasks(
    meta: dict[str, dict[str, Any]],
    events: Iterable[Any],
) -> dict[str, str]:
    out: dict[str, str] = {}
    for instance_id, item_meta in meta.items():
        payload = item_meta.get("last_heartbeat_payload")
        if isinstance(payload, dict):
            task_id = str(payload.get("current_task_id") or payload.get("task_id") or "")
            if task_id:
                out[instance_id] = task_id
    for event in events:
        if _etype(event) not in {"task.dispatched", "worker.started", "worker.progress"}:
            continue
        actor = _actor(event)
        task_id = _task_id(event) or str(_payload(event).get("task_id") or "")
        if actor and task_id:
            out[actor] = task_id
    return out


def _first_session_path(meta: dict[str, Any]) -> Path | None:
    for key in ("session_path", "transcript_path", "log_path", "output_path"):
        raw = str(meta.get(key) or "")
        if raw:
            return Path(raw).expanduser()
    return None


def _session_ref(path: Path | None, *, project_root: Path) -> dict[str, Any]:
    if path is None:
        return {"exists": False, "path": "", "path_hash": ""}
    exists = path.exists()
    return {
        "exists": exists,
        "path": _display_path(path, project_root=project_root),
        "path_hash": _sha256_text(str(path)),
        "name": path.name,
    }


def _terminal_excerpt(
    *,
    instance_id: str,
    backend: str,
    path: Path,
    project_root: Path,
    max_bytes: int,
) -> dict[str, Any]:
    base = {
        "instance_id": instance_id,
        "backend": backend,
        "path": _display_path(path, project_root=project_root),
        "path_hash": _sha256_text(str(path)),
        "exists": path.exists(),
        "max_bytes": max_bytes,
        "read_only": True,
    }
    if not path.exists():
        return {**base, "status": "missing", "excerpt": "", "excerpt_sha256": ""}
    try:
        size = path.stat().st_size
        start = max(0, size - max_bytes)
        with path.open("rb") as handle:
            handle.seek(start)
            raw = handle.read(max_bytes)
    except OSError as exc:
        return {**base, "status": "unreadable", "reason": str(exc), "excerpt": ""}
    text = raw.decode("utf-8", errors="replace")
    redacted = redact_text(text)
    return {
        **base,
        "status": "ok",
        "start_offset": start,
        "end_offset": start + len(raw),
        "truncated": start > 0,
        "excerpt": redacted,
        "excerpt_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _tmux_projection(
    *,
    config: Any | None,
    meta: dict[str, dict[str, Any]],
    tmux_output: str | None,
) -> dict[str, Any]:
    configured = str(getattr(getattr(config, "session", None), "tmux_session", "") or "")
    observed = tmux_output if tmux_output is not None else _tmux_list_sessions()
    sessions = _parse_tmux_sessions(observed)
    role_sessions = sorted({
        str(item.get("tmux_session") or "")
        for item in meta.values()
        if str(item.get("tmux_session") or "")
    })
    names = {str(item.get("session_name") or "") for item in sessions}
    return {
        "configured_session": configured,
        "available": observed is not None,
        "sessions": sessions,
        "role_sessions": role_sessions,
        "configured_active": bool(configured and configured in names),
        "probe": "tmux list-sessions",
        "read_only": True,
    }


def _tmux_list_sessions() -> str | None:
    if shutil.which("tmux") is None:
        return None
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}\t#{session_attached}\t#{session_windows}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
        )
    except Exception:
        return None
    return result.stdout if result.returncode == 0 else None


def _parse_tmux_sessions(output: str | None) -> list[dict[str, Any]]:
    if not output:
        return []
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        name = parts[0].strip() if parts else ""
        if not name:
            continue
        rows.append({
            "session_name": name,
            "attached": _to_int(parts[1] if len(parts) > 1 else "") > 0,
            "windows": _to_int(parts[2] if len(parts) > 2 else ""),
        })
    return rows


def _heartbeat_stale(value: str, *, threshold_seconds: int = 180) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds() > threshold_seconds


def _display_path(path: Path, *, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except (OSError, ValueError):
        home = Path.home()
        try:
            return "~/" + str(path.resolve().relative_to(home.resolve()))
        except (OSError, ValueError):
            return path.name


def _parent_role(instance_id: str) -> str:
    return instance_id.split("-", 1)[0] if "-" in instance_id else instance_id


def _first_int(mapping: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _to_int(value: str) -> int:
    return int(value) if value.strip().isdigit() else 0


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _etype(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("type") or "")
    return str(getattr(event, "type", "") or "")


def _payload(event: Any) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _actor(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("actor") or "")
    return str(getattr(event, "actor", "") or "")


def _task_id(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("task_id") or "")
    return str(getattr(event, "task_id", "") or "")


__all__ = [
    "RUNTIME_RESOURCE_SCHEMA_VERSION",
    "build_runtime_resource_projection",
]
