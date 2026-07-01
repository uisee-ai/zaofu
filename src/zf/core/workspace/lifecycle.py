"""Project lifecycle projection for workspace views."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.config.loader import load_config
from zf.core.workspace.registry import WorkspaceProject
from zf.core.workspace.runtime_manager import RuntimeManager


@dataclass(frozen=True)
class ProjectLifecycle:
    has_config: bool
    config_loadable: bool
    state_dir_exists: bool
    initialized: bool
    can_open_board: bool
    runtime_state: str
    reason: str = ""
    state_dir_resolved: str = ""
    missing_truth_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_config": self.has_config,
            "config_loadable": self.config_loadable,
            "state_dir_exists": self.state_dir_exists,
            "initialized": self.initialized,
            "can_open_board": self.can_open_board,
            "runtime_state": self.runtime_state,
            "reason": self.reason,
            "state_dir_resolved": self.state_dir_resolved,
            "missing_truth_files": list(self.missing_truth_files),
        }


def project_lifecycle(project: WorkspaceProject) -> ProjectLifecycle:
    """Resolve a registry row into a read-only lifecycle projection.

    The registry's state_dir_hint is deliberately not trusted. Runtime state is
    resolved from the current zf.yaml on every call.
    """

    config_path = Path(project.config_path).expanduser()
    if not config_path.exists():
        return ProjectLifecycle(
            has_config=False,
            config_loadable=False,
            state_dir_exists=False,
            initialized=False,
            can_open_board=False,
            runtime_state="unavailable",
            reason=f"config not found: {config_path}",
            state_dir_resolved=str(Path(project.state_dir_hint).expanduser()),
        )

    project_root = Path(project.root).expanduser().resolve()
    cache_key = _lifecycle_cache_key(project, config_path=config_path, project_root=project_root)
    cached = _get_cached_lifecycle(cache_key)
    if cached is not None:
        return cached
    try:
        config = load_config(config_path.resolve())
    except Exception as exc:
        return ProjectLifecycle(
            has_config=True,
            config_loadable=False,
            state_dir_exists=False,
            initialized=False,
            can_open_board=False,
            runtime_state="unavailable",
            reason=str(exc),
            state_dir_resolved=str(Path(project.state_dir_hint).expanduser()),
        )

    state_dir = Path(config.project.state_dir)
    if not state_dir.is_absolute():
        state_dir = project_root / state_dir
    state_dir = state_dir.resolve()
    missing = tuple(
        name for name in ("kanban.json", "events.jsonl")
        if not (state_dir / name).exists()
    )
    state_dir_exists = state_dir.exists()
    initialized = state_dir_exists and not missing
    runtime = RuntimeManager().status(
        state_dir=state_dir,
        config=config,
        project_id=project.project_id,
    )
    reason = ""
    if not state_dir_exists:
        reason = f"state dir not found: {state_dir}"
    elif missing:
        reason = "missing runtime truth files: " + ", ".join(missing)
    lifecycle = ProjectLifecycle(
        has_config=True,
        config_loadable=True,
        state_dir_exists=state_dir_exists,
        initialized=initialized,
        can_open_board=initialized,
        runtime_state=runtime.state,
        reason=reason,
        state_dir_resolved=str(state_dir),
        missing_truth_files=missing,
    )
    _set_cached_lifecycle(cache_key, lifecycle)
    return lifecycle


_CACHE_TTL_SECONDS = 3.0
_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[Any, ...], tuple[float, ProjectLifecycle]] = {}


def clear_project_lifecycle_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _get_cached_lifecycle(key: tuple[Any, ...]) -> ProjectLifecycle | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item is None:
            return None
        expires_at, value = item
        if now >= expires_at:
            _CACHE.pop(key, None)
            return None
        return value


def _set_cached_lifecycle(key: tuple[Any, ...], value: ProjectLifecycle) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + _CACHE_TTL_SECONDS, value)


def _lifecycle_cache_key(
    project: WorkspaceProject,
    *,
    config_path: Path,
    project_root: Path,
) -> tuple[Any, ...]:
    stat = config_path.stat()
    return (
        project.project_id,
        str(project_root),
        str(config_path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
        _file_digest(config_path),
    )


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
