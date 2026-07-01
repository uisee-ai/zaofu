"""Per-worker launch artifact projection."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.state.atomic_io import atomic_write_text


_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|credential|auth|bearer|cookie)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|xox[baprs]-[A-Za-z0-9-]{10,}|gh[pousr]_[A-Za-z0-9_]{12,})"
)
_ENV_PREFIXES = (
    "ZF_",
    "CODEX_",
    "CLAUDE_",
    "OPENAI_",
    "ANTHROPIC_",
    "GITHUB_",
    "GH_",
    "FEISHU_",
)


def write_launch_artifact(
    *,
    state_dir: Path,
    project_root: Path,
    role: RoleConfig,
    argv: list[str],
    cwd: Path,
    session_id: str | None,
    is_resume: bool,
    transport: object | None = None,
) -> Path:
    """Write latest + attempt launch artifacts for one worker spawn."""

    runtime_dir = state_dir / "workdirs" / role.instance_id / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    attempt = _next_attempt(runtime_dir)
    payload = {
        "schema_version": "worker-launch.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "attempt": attempt,
        "role": role.name,
        "instance_id": role.instance_id,
        "backend": role.backend,
        "cwd": str(cwd),
        "project_root": str(project_root),
        "state_dir": str(state_dir),
        "session_id": session_id or "",
        "is_resume": is_resume,
        "skills": list(role.skills),
        "instructions_ref": str(state_dir / "instructions" / f"{role.instance_id}.md"),
        "skills_manifest_ref": str(runtime_dir / "skills-manifest.json"),
        "transport": _transport_summary(transport),
        "argv": [_redact_arg(arg) for arg in argv],
        "env": _env_summary(),
    }
    latest_path = runtime_dir / "launch.json"
    attempt_path = runtime_dir / f"launch-attempt-{attempt}.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text(attempt_path, text)
    atomic_write_text(latest_path, text)
    return latest_path


def _next_attempt(runtime_dir: Path) -> int:
    attempts = []
    for path in runtime_dir.glob("launch-attempt-*.json"):
        stem = path.stem.rsplit("-", 1)[-1]
        if stem.isdigit():
            attempts.append(int(stem))
    return max(attempts, default=0) + 1


def _transport_summary(transport: object | None) -> dict[str, str]:
    if transport is None:
        return {}
    summary: dict[str, str] = {"type": type(transport).__name__}
    session_name = getattr(transport, "session_name", "")
    if session_name:
        summary["session_name"] = str(session_name)
    dry_run = getattr(transport, "dry_run", None)
    if dry_run is not None:
        summary["dry_run"] = str(bool(dry_run)).lower()
    return summary


def _env_summary() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key in sorted(os.environ):
        if not _include_env_key(key):
            continue
        value = os.environ.get(key, "")
        rows.append({
            "key": key,
            "value": _redact_value(key, value),
        })
    return rows


def _include_env_key(key: str) -> bool:
    if key.startswith(_ENV_PREFIXES):
        return True
    return bool(_SENSITIVE_KEY_RE.search(key))


def _redact_arg(arg: str) -> str:
    if "=" in arg:
        key, value = arg.split("=", 1)
        return f"{key}={_redact_value(key, value)}"
    if _SENSITIVE_VALUE_RE.search(arg):
        return _SENSITIVE_VALUE_RE.sub("<redacted>", arg)
    return arg


def _redact_value(key: str, value: Any) -> str:
    text = str(value)
    if _SENSITIVE_KEY_RE.search(key):
        return "<redacted>" if text else ""
    if _SENSITIVE_VALUE_RE.search(text):
        return _SENSITIVE_VALUE_RE.sub("<redacted>", text)
    return text
