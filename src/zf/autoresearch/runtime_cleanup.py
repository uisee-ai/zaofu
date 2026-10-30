"""Stop an experiment's runtime independently of candidate/archive retention."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.project_context import isolated_project_subprocess_env
from zf.core.state.session import SessionStore, ZfNotInitialized


def stop_experiment_runtime(
    worktree: Path, *, runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if worktree.is_symlink():
        return {"status": "retained", "reason": "worktree_symlink"}
    root = worktree.resolve()
    config_path = root / "zf.yaml"
    if not config_path.is_file():
        return {"status": "not_started"}
    try:
        config = load_config(config_path)
        state = (root / config.project.state_dir).resolve()
        state.relative_to(root)
        if not (state / "session.yaml").is_file():
            return {"status": "not_started"}
        session = SessionStore(state / "session.yaml").load()
        if session is None or Path(session.project_root).resolve() != root:
            return {"status": "retained", "reason": "runtime_owner_mismatch"}
        outcome = runner(
            [sys.executable, "-m", "zf.cli.main", "stop", "--fast", "--include-run-manager"],
            cwd=root, env=isolated_project_subprocess_env(), capture_output=True,
            text=True, check=False, timeout=90,
        )
        return {"status": "stopped" if outcome.returncode == 0 else "retained",
                "returncode": outcome.returncode,
                "reason": "" if outcome.returncode == 0 else "runtime_stop_failed"}
    except (OSError, ValueError, TypeError, ConfigError, ZfNotInitialized, yaml.YAMLError, subprocess.TimeoutExpired) as exc:
        return {"status": "retained", "reason": type(exc).__name__}
