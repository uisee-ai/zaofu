"""Project context resolution for CLI entry points.

`zf.yaml` is the control-plane source for the runtime state directory.
This module keeps that lookup in one place so commands do not silently
fall back to a separate `$CWD/.zf` tree when `project.state_dir` is set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import ZfConfig


@dataclass(frozen=True)
class ProjectContext:
    project_root: Path
    config_path: Path
    config: ZfConfig | None
    state_dir: Path


def _dotenv_unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    # strip a trailing inline comment from an unquoted value (KEY=val # note)
    return value.split(" #", 1)[0].strip()


def load_env_file(path: Path) -> dict[str, str]:
    """Load simple KEY=VALUE entries from a dotenv file into ``os.environ``.

    Does NOT override variables already present in the environment. Returns the
    keys it set. Canonical home for the loader that ``zf web`` / ``zf feishu``
    (and now ``zf start``) use so CLI entry points get project ``.env`` vars
    (FEISHU creds, ZF_OWNER_VISIBLE_CHAT, …) regardless of the launch shell.
    """
    if not path.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _dotenv_unquote(value)
        loaded[key] = os.environ[key]
    return loaded


def load_project_env(project_root: Path) -> dict[str, str]:
    """Load ``<project_root>/.env`` into ``os.environ`` (non-overriding)."""
    return load_env_file(Path(project_root) / ".env")


def _resolve_state_path(project_root: Path, state_dir: str | Path) -> Path:
    path = Path(state_dir)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _find_project_root(start: Path) -> Path:
    """Return the nearest ancestor containing zf.yaml, or ``start``.

    CLI commands are often run from the project root, but worker shells and
    operator terminals can start from subdirectories. ``zf.yaml`` remains the
    control-plane anchor, so prefer the directory that owns it when present.
    """
    return _find_config_root(start) or start.resolve()


def _find_config_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "zf.yaml").exists():
            return candidate
    return None


def _env_path(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _resolve_project_root_path(start: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = start / path
    return path.resolve()


def resolve_project_context(
    *,
    cwd: Path | None = None,
    explicit_state_dir: str | Path | None = None,
    require_config: bool = False,
    load_config_with_explicit: bool = False,
) -> ProjectContext:
    """Resolve project root, optional config, and effective state dir.

    `explicit_state_dir` is intentionally first-class: commands that
    already expose `--state-dir` must be able to point at a runtime tree
    even if the current `zf.yaml` is absent or temporarily invalid.
    """
    start = (cwd or Path.cwd()).resolve()
    local_project_root = _find_config_root(start)
    env_project_root_raw = _env_path("ZF_PROJECT_ROOT")
    env_project_root = (
        _resolve_project_root_path(start, env_project_root_raw)
        if env_project_root_raw is not None
        else None
    )
    # Worker/replay shells can inherit an outer project env while tests or
    # workspace requests intentionally operate from another project root. When
    # a runtime state dir is also supplied, though, the env describes an active
    # orchestrator session and detached workdirs must resolve back to that owner
    # project rather than treating their copied zf.yaml as the control plane.
    env_conflicts_with_local = (
        local_project_root is not None
        and env_project_root is not None
        and local_project_root != env_project_root
    )
    env_state_dir_raw = _env_path("ZF_STATE_DIR")
    env_runtime_context = (
        env_project_root is not None
        and (env_state_dir_raw is not None or explicit_state_dir is not None)
    )
    if env_project_root is not None and (
        local_project_root is None
        or not env_conflicts_with_local
        or env_runtime_context
    ):
        project_root = env_project_root
    elif local_project_root is not None:
        project_root = local_project_root
    else:
        project_root = start
    config_path = project_root / "zf.yaml"

    config: ZfConfig | None = None
    env_state_dir = (
        env_state_dir_raw
        if explicit_state_dir is None and project_root == env_project_root
        else None
    )
    should_load_config = (
        explicit_state_dir is None or require_config or load_config_with_explicit
    )
    if should_load_config:
        if config_path.exists():
            config = load_config(config_path)
        elif require_config:
            raise ConfigError(f"Config file not found: {config_path}")

    if explicit_state_dir is not None:
        state_dir = _resolve_state_path(project_root, explicit_state_dir)
    elif env_state_dir is not None:
        state_dir = _resolve_state_path(project_root, env_state_dir)
    elif config is not None:
        state_dir = _resolve_state_path(project_root, config.project.state_dir)
    else:
        state_dir = _resolve_state_path(project_root, ".zf")

    return ProjectContext(
        project_root=project_root,
        config_path=config_path,
        config=config,
        state_dir=state_dir,
    )


def resolve_state_dir(
    *,
    cwd: Path | None = None,
    explicit_state_dir: str | Path | None = None,
    require_config: bool = False,
    load_config_with_explicit: bool = False,
) -> Path:
    return resolve_project_context(
        cwd=cwd,
        explicit_state_dir=explicit_state_dir,
        require_config=require_config,
        load_config_with_explicit=load_config_with_explicit,
    ).state_dir
