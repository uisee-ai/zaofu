"""Worker-facing ZaoFu CLI command resolution.

Workers run inside project/worktree shells where a bare ``zf`` may resolve to an
older globally installed CLI. ``ZF_CLI_CMD`` lets the harness pass the exact CLI
command that launched the current runtime into prompts, tmux panes, and hooks.
"""

from __future__ import annotations

import os
import shlex
import sys
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
    _prepend_runtime_bin_to_path()
    return command


def default_zf_cli_cmd() -> str:
    """Return a CLI bound to the Python environment running the harness."""
    python = Path(sys.executable)
    sibling_cli = python.parent / "zf"
    if sibling_cli.is_file():
        return shlex.quote(str(sibling_cli))
    return f"{shlex.quote(str(python))} -m zf.cli.main"


def _prepend_runtime_bin_to_path() -> None:
    """Make a bare ``zf`` resolve beside the active harness interpreter."""
    runtime_bin = str(Path(sys.executable).parent)
    entries = [item for item in os.environ.get("PATH", "").split(os.pathsep) if item]
    os.environ["PATH"] = os.pathsep.join(
        [runtime_bin, *(item for item in entries if item != runtime_bin)]
    )


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
