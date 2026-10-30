"""Compatibility receipt for the approved-config Git checkpoint."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from zf.runtime.git_capture import git_env
from zf.runtime.workflow_config_checkpoint import checkpoint_config


class ConfigBaselineError(ValueError):
    """The approved config cannot safely establish a Git delivery baseline."""


def establish_config_baseline(
    target: Path, *, base_digest: str, target_digest: str, proposal_digest: str,
) -> dict[str, Any]:
    try:
        commit = checkpoint_config(
            target.parent, base_digest=base_digest, target_digest=target_digest,
            proposal_digest=proposal_digest,
        )
    except (OSError, ValueError) as exc:
        raise ConfigBaselineError(str(exc)) from exc
    if not commit:
        return {"status": "not_required", "reason": "project config is not tracked in Git"}
    prefix = subprocess.check_output(
        ["git", "-C", str(target.parent), "rev-parse", "--show-prefix"],
        env=git_env(), text=True, timeout=30,
    ).strip()
    return {"status": "ready", "commit": commit, "config_path": prefix + target.name,
            "proposal_digest": proposal_digest}
