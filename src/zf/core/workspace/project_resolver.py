"""Resolve registered workspace projects back to ProjectContext."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.config.loader import load_config
from zf.core.config.project_context import ProjectContext
from zf.core.workspace.registry import WorkspaceProject, WorkspaceRegistry


@dataclass(frozen=True)
class ProjectResolution:
    project: WorkspaceProject
    context: ProjectContext

    @property
    def project_id(self) -> str:
        return self.project.project_id


class ProjectResolver:
    """Resolve ``project_id`` through registry, then reload ``zf.yaml``."""

    def __init__(self, registry: WorkspaceRegistry | None = None) -> None:
        self.registry = registry or WorkspaceRegistry()

    def resolve(self, project_id: str) -> ProjectResolution:
        project = self.registry.get(project_id)
        if project is None:
            raise KeyError(f"project {project_id!r} is not registered")
        config_path = Path(project.config_path).expanduser().resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"registered config not found: {config_path}")
        project_root = Path(project.root).expanduser().resolve()
        cache_key = _resolution_cache_key(project, config_path=config_path, project_root=project_root)
        cached = _get_cached_resolution(cache_key)
        if cached is not None:
            return cached
        config = load_config(config_path)
        state_dir = Path(config.project.state_dir)
        if not state_dir.is_absolute():
            state_dir = project_root / state_dir
        context = ProjectContext(
            project_root=project_root,
            config_path=config_path,
            config=config,
            state_dir=state_dir.resolve(),
        )
        resolved = ProjectResolution(project=project, context=context)
        _set_cached_resolution(cache_key, resolved)
        return resolved


_CACHE_TTL_SECONDS = 3.0
_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[Any, ...], tuple[float, ProjectResolution]] = {}


def clear_project_resolver_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _get_cached_resolution(key: tuple[Any, ...]) -> ProjectResolution | None:
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


def _set_cached_resolution(key: tuple[Any, ...], value: ProjectResolution) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + _CACHE_TTL_SECONDS, value)


def _resolution_cache_key(
    project: WorkspaceProject,
    *,
    config_path: Path,
    project_root: Path,
) -> tuple[Any, ...]:
    stat = config_path.stat()
    return (
        project.project_id,
        str(project_root),
        str(config_path),
        stat.st_mtime_ns,
        stat.st_size,
        _file_digest(config_path),
    )


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
