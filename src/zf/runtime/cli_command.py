"""Worker-facing ZaoFu CLI command resolution.

Workers run inside project/worktree shells where a bare ``zf`` may resolve to an
older globally installed CLI. ``ZF_CLI_CMD`` lets the harness pass the exact CLI
command that launched the current runtime into prompts, tmux panes, and hooks.
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path


ZF_CLI_CMD_ENV = "ZF_CLI_CMD"


def zf_cli_cmd() -> str:
    """Return the command snippets should render for worker-facing CLI calls."""
    return os.environ.get(ZF_CLI_CMD_ENV, "").strip() or "zf"


def set_default_zf_cli_cmd() -> str:
    """Populate ``ZF_CLI_CMD`` when the launcher did not provide one."""
    configured = os.environ.get(ZF_CLI_CMD_ENV, "").strip()
    if configured:
        return configured
    command = default_zf_cli_cmd()
    os.environ[ZF_CLI_CMD_ENV] = command
    return command


def default_zf_cli_cmd() -> str:
    """Prefer the source checkout CLI when running from a local repo."""
    source_root = discover_source_root()
    if source_root is not None and shutil.which("uv"):
        return f"uv --project {shlex.quote(str(source_root))} run zf"
    return "zf"


def discover_source_root(start: Path | None = None) -> Path | None:
    current = (start or Path(__file__)).resolve()
    candidates = [current] if current.is_dir() else list(current.parents)
    if current.is_dir():
        candidates.extend(current.parents)
    for parent in candidates:
        if (
            (parent / "pyproject.toml").exists()
            and (parent / "src" / "zf").is_dir()
        ):
            return parent
    return None
