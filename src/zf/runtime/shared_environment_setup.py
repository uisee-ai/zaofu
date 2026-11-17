"""Opt-in receipts for setup scripts whose outputs are shared resources only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.environment_resources import environment_lock_plan


def resource_epochs(worktree: Path) -> dict[str, str]:
    result = {}
    for name in environment_lock_plan(worktree)['locks'][1:]:
        path = Path(name).with_suffix('.epoch')
        result[name] = path.read_text() if path.exists() else ''
    return result


def invalidate_shared_setup(worktree: Path) -> None:
    # Caller holds exclusive resource locks. In-place installs also invalidate
    # receipts; inode-only checks cannot detect a changed dependency tree.
    for name in environment_lock_plan(worktree)['locks'][1:]:
        atomic_write_text(Path(name).with_suffix('.epoch'), uuid4().hex)


def _identity(worktree: Path, digest: str) -> tuple[Path, dict]:
    plan = environment_lock_plan(worktree)
    resources = {target: plan['instances'][logical] for logical, target in plan['bindings'].items()}
    if not resources or any(identity is None for identity in resources.values()):
        raise ValueError('shared setup requires existing declared resources')
    key = hashlib.sha256(json.dumps(sorted(resources)).encode()).hexdigest()
    path = Path(plan['locks'][1]).parent / 'setup-receipts' / f'{key}.json'
    return path, {'schema': 'shared-setup.v1', 'input_digest': digest,
                  'declarations': {str(Path(logical).relative_to(worktree.resolve())): target
                                   for logical, target in plan['bindings'].items()},
                  'resources': resources, 'epochs': resource_epochs(worktree)}


def shared_setup_matches(worktree: Path, digest: str) -> bool:
    try:
        path, current = _identity(worktree, digest)
        return json.loads(path.read_text()) == current
    except (OSError, ValueError):
        return False


def record_shared_setup(worktree: Path, digest: str) -> None:
    path, body = _identity(worktree, digest)
    atomic_write_text(path, json.dumps(body, sort_keys=True) + '\n')
