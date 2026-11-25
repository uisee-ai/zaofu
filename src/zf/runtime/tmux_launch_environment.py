"""Carry the configured runtime and native input libraries into tmux workers."""

import os
from pathlib import Path
import shlex

_AGENT_ENV_KEYS = (
    "PYTHONPATH",
    "PATH",
    "VIRTUAL_ENV",
    "ZF_PROJECT_ROOT",
    "ZF_STATE_DIR",
    "ZF_CLI_CMD",
    "FUSE_LIBRARY_PATH",
)


def agent_env_prefix(cwd: Path | None = None) -> str:
    """Carry selected launch env into tmux panes.

    tmux panes inherit the long-lived tmux server environment, not
    reliably the current ``zf start`` process environment. A stale server
    can therefore launch Codex without the project PYTHONPATH; commands
    such as ``zf emit`` then import an older installed ZaoFu. Keep this
    narrow: only runtime resolution variables, never arbitrary secrets.
    """
    workspace_venv = (cwd / ".venv").resolve() if cwd is not None else None
    assignments: list[str] = []
    unset_keys: list[str] = []
    for key in _AGENT_ENV_KEYS:
        value = os.environ.get(key)
        if key == "VIRTUAL_ENV" and value and workspace_venv is not None:
            if Path(value).resolve() != workspace_venv:
                unset_keys.append(key)
                continue
        if value:
            assignments.append(shlex.quote(f"{key}={value}"))
    if not assignments and not unset_keys:
        return ""
    parts = ["/usr/bin/env"]
    for key in unset_keys:
        parts.extend(["-u", key])
    parts.extend(assignments)
    return " ".join(parts)
