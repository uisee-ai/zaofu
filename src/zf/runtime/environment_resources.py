"""Declared dependency resources, resolved independently of worker placement."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import FileLock, lock_path_for
from zf.runtime.environment_preparation import environment_preparation_dir
from zf.runtime.environment_exec import path_identity


def relative_resource_paths(paths: object) -> list[str]:
    if not isinstance(paths, list) or any(
        not isinstance(p, str) or not p.strip() or Path(p).is_absolute()
        or '..' in Path(p).parts or not Path(p).parts for p in paths
    ):
        raise ValueError('environment paths must list nonempty workspace-relative paths')
    return sorted({str(Path(p)) for p in paths})


def resource_manifest(worktree: Path) -> Path:
    return environment_preparation_dir(worktree) / 'resources.json'


def read_resource_paths(worktree: Path) -> list[str]:
    try:
        raw = resource_manifest(worktree).read_bytes()
    except FileNotFoundError:
        return []
    return _parse_resources(raw)


def _parse_resources(raw: bytes) -> list[str]:
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('schema') != 'environment-resources.v1':
        raise ValueError('invalid environment resource manifest')
    return relative_resource_paths(data.get('paths'))


def register_environment_resources(worktree: Path, paths: list[str]) -> None:
    declared = relative_resource_paths(paths)
    if not declared:
        return
    # Retain older declarations conservatively: removing a consumer does not
    # prove that its resources have been retired.
    if set(declared).issubset(read_resource_paths(worktree)):
        return
    with FileLock(lock_path_for(environment_preparation_dir(worktree) / 'prepare'), timeout_seconds=0):
        merged = sorted(set(read_resource_paths(worktree)) | set(declared))
        atomic_write_text(resource_manifest(worktree), json.dumps({
            'schema': 'environment-resources.v1', 'paths': merged,
        }) + '\n')


def environment_lock_plan(worktree: Path) -> dict:
    root = worktree.resolve()
    manifest = resource_manifest(root)
    try:
        raw = manifest.read_bytes()
    except FileNotFoundError:
        raw = None
    paths = _parse_resources(raw) if raw is not None else []
    bindings = {str(root / p): str((root / p).resolve()) for p in paths}
    locks = set()
    for target in bindings.values():
        path = Path(target)
        # Keep the lock outside a dependency directory that an installer may
        # replace. The same physical target yields the same key for all users.
        parent = path.parent
        while not parent.exists():
            parent = parent.parent
        key = hashlib.sha256(target.encode()).hexdigest()
        result = subprocess.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'],
            cwd=parent, capture_output=True, text=True, timeout=10, check=False)
        storage = (Path(result.stdout.strip()) / 'zf-resource-locks' if result.returncode == 0
                   else _host_resource_lock_dir())
        locks.add(str(storage / (key + '.lock')))
    return {
        'locks': [str(lock_path_for(environment_preparation_dir(root) / 'prepare')), *sorted(locks)],
        'bindings': bindings,
        'instances': {logical: path_identity(logical) for logical in bindings},
        'manifest': str(manifest),
        'manifest_digest': hashlib.sha256(raw).hexdigest() if raw is not None else None,
    }


def _host_resource_lock_dir() -> Path:
    # Never create absent dependency parents merely to acquire a lock: that
    # could make a required directory appear installed. Keep non-Git locks
    # outside workspaces in a private, same-user host namespace.
    path = Path(tempfile.gettempdir()) / f'zf-environment-locks-{os.getuid()}'
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError('unsafe host environment lock directory')
    return path
