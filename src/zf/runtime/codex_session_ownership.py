"""Role-local ownership checks for resumable Codex sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.core.state import role_sessions


def codex_session_rollout_for_role(
    registry: Any,
    *,
    role_sessions_root: Path,
    instance_id: str,
    session_id: str,
) -> Path | None:
    """Return the owned rollout path for one role/session, if it exists."""

    allowed_roots = (
        Path(role_sessions_root).resolve(),
        Path(role_sessions.CODEX_SESSIONS_ROOT).resolve(),
    )

    def matches(path: Path) -> bool:
        try:
            resolved = path.resolve()
            owned = any(resolved.is_relative_to(root) for root in allowed_roots)
            return (
                path.is_file()
                and owned
                and session_id in path.stem
                and registry._rollout_matches_project(path)
            )
        except OSError:
            return False

    cached = registry.get_path(instance_id)
    if cached is not None and matches(cached):
        return cached
    for root in allowed_roots:
        try:
            candidates = root.glob(f"*/*/*/rollout-*-{session_id}.jsonl")
        except OSError:
            continue
        for path in candidates:
            if matches(path):
                return path
    return None


def codex_session_exists_for_role(
    registry: Any,
    *,
    role_sessions_root: Path,
    instance_id: str,
    session_id: str,
) -> bool:
    """Return true only when the rollout is visible to this role's home."""

    return codex_session_rollout_for_role(
        registry,
        role_sessions_root=role_sessions_root,
        instance_id=instance_id,
        session_id=session_id,
    ) is not None


def codex_session_terminal_error_for_role(
    registry: Any,
    *,
    role_sessions_root: Path,
    instance_id: str,
    session_id: str,
) -> str:
    """Return the latest terminal provider error from an owned rollout."""

    path = codex_session_rollout_for_role(
        registry,
        role_sessions_root=role_sessions_root,
        instance_id=instance_id,
        session_id=session_id,
    )
    if path is None:
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 256_000))
            rows = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(rows):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = row.get("payload") if isinstance(row, dict) else None
        if not isinstance(payload, dict) or payload.get("type") != "task_complete":
            continue
        error = payload.get("error")
        if not isinstance(error, dict):
            return ""
        return str(error.get("message") or error.get("codex_error_info") or "")[:500]
    return ""


__all__ = [
    "codex_session_exists_for_role",
    "codex_session_rollout_for_role",
    "codex_session_terminal_error_for_role",
]
