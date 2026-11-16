"""A setup receipt is reusable only for the same declared resource instances."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.environment_preparation import environment_preparation_dir
from zf.runtime.environment_resources import read_resource_paths
from zf.runtime.environment_exec import path_identity


def _receipt(worktree: Path, marker: Path) -> Path:
    key = hashlib.sha256(str(marker.resolve()).encode()).hexdigest()
    return environment_preparation_dir(worktree) / ('setup-' + key + '.json')


def _snapshot(worktree: Path) -> dict:
    from zf.runtime.shared_environment_setup import resource_epochs
    epochs = resource_epochs(worktree)
    return {name: {'target': str((worktree / name).resolve()),
                   'identity': path_identity(str(worktree / name)), 'epochs': epochs}
            for name in read_resource_paths(worktree)}


def setup_environment_matches(worktree: Path, marker: Path) -> bool:
    current = _snapshot(worktree)
    if not current:
        return True  # Legacy setup without declared resources keeps its contract.
    try:
        saved = json.loads(_receipt(worktree, marker).read_text())
    except (FileNotFoundError, ValueError):
        return False
    return saved == {'schema': 'setup-environment.v1', 'resources': current}


def invalidate_setup_environment(worktree: Path, marker: Path) -> None:
    from zf.runtime.shared_environment_setup import invalidate_shared_setup
    invalidate_shared_setup(worktree)
    _receipt(worktree, marker).unlink(missing_ok=True)


def record_setup_environment(worktree: Path, marker: Path) -> None:
    atomic_write_text(_receipt(worktree, marker), json.dumps({
        'schema': 'setup-environment.v1', 'resources': _snapshot(worktree),
    }) + '\n')
