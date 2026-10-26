"""Bounded, non-destructive Git evidence for supervised maintenance.

A captured snapshot is not a provider-stop acknowledgement or permission to
replace a worker. Ignored files and Git history remain in the original worktree.
"""

from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

from zf.runtime.sidecar_refs import write_sidecar_json

MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


def checkpoint_root(state_dir: Path, project_root: Path, config: Any,
                    task_id: str, assigned_worker: str) -> Path:
    """Resolve the canonical assignee; never substitute the main checkout."""
    if config is None:
        return project_root
    from zf.core.task.store import TaskStore
    from zf.runtime.workdirs import WorkdirManager

    task = TaskStore(state_dir / "kanban.json").get(task_id)
    owner = str(task.assigned_to or "") if task else ""
    if assigned_worker and owner != assigned_worker:
        raise ValueError("checkpoint worker does not match canonical task assignee")
    worker = assigned_worker or owner
    if not worker or not config.runtime.workdirs.enabled:
        return project_root
    role = next((r for r in config.roles if r.instance_id == worker), None)
    if role is None:
        raise ValueError("checkpoint worker is not registered")
    plan = WorkdirManager(state_dir=state_dir, project_root=project_root, config=config).plan(role)
    if plan.mode == "dry-run":
        return project_root
    root = Path(plan.project_path)
    if not root.is_dir():
        raise ValueError("checkpoint worker workspace is missing")
    return root


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True,
                            check=True, timeout=30)
    if len(result.stdout) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds byte limit")
    return result.stdout


def _capture(root: Path, state_dir: Path) -> dict[str, Any]:
    if Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()).resolve() != root:
        raise ValueError("checkpoint root is not a Git worktree root")
    paths = ["--", "."]
    if state_dir.is_relative_to(root):
        relative = state_dir.relative_to(root).as_posix()
        if relative == ".":
            raise ValueError("runtime state cannot be the checkpoint source root")
        paths.append(f":(top,exclude,literal){relative}")
    head = _git(root, "rev-parse", "HEAD").decode().strip()
    staged = _git(root, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", *paths)
    unstaged = _git(root, "diff", "--binary", "--no-ext-diff", "--no-textconv", *paths)
    names = _git(root, "ls-files", "--others", "--exclude-standard", "-z", *paths)
    total = len(staged) + len(unstaged) + len(names)
    entries = []
    for raw in names.split(b"\0"):
        if not raw:
            continue
        name = os.fsdecode(raw)
        path = root / name
        if not path.parent.resolve().is_relative_to(root):
            raise ValueError("untracked path escapes checkpoint root")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            raise ValueError("unsupported untracked file kind")
        total += info.st_size
        if total > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot exceeds byte limit")
        if stat.S_ISLNK(info.st_mode):
            entries.append({"path": name, "kind": "symlink", "target": os.readlink(path)})
        else:
            # O_NOFOLLOW prevents a concurrent replacement from reading outside
            # the workspace; a second capture below rejects changing inputs.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                body = stream.read(MAX_SNAPSHOT_BYTES + 1)
            if len(body) != info.st_size:
                raise ValueError("workspace changed during capture")
            entries.append({"path": name, "kind": "file", "mode": stat.S_IMODE(info.st_mode),
                            "content_base64": base64.b64encode(body).decode("ascii")})
    if total > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds byte limit")
    # Dirty nested repositories cannot be reconstructed by a superproject patch.
    if _git(root, "submodule", "status").strip():
        raise ValueError("submodules require a separate checkpoint")
    return {"head": head, "staged_patch_base64": base64.b64encode(staged).decode("ascii"),
            "unstaged_patch_base64": base64.b64encode(unstaged).decode("ascii"),
            "untracked": entries}


def capture_workspace(state_dir: Path, root: Path, checkpoint_id: str) -> dict[str, Any]:
    root, state_dir = root.resolve(), state_dir.resolve()
    payload: dict[str, Any] = {"schema_version": "maintenance-workspace.v1",
                               "worktree_root": str(root), "status": "unavailable",
                               "provider_quiescence": "unverified",
                               "excluded": ["ignored files", "Git history", "runtime state"]}
    try:
        first = _capture(root, state_dir)
        if _capture(root, state_dir) != first:
            raise ValueError("workspace changed during capture")
        if len(json.dumps(first, ensure_ascii=True).encode()) > 3 * MAX_SNAPSHOT_BYTES // 2:
            raise ValueError("encoded snapshot exceeds byte limit")
        payload.update(first, status="captured")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        payload["reason"] = str(exc)
    return write_sidecar_json(state_dir, f"snapshots/maintenance/{checkpoint_id}.json", payload,
                              kind="maintenance_workspace", schema_version="maintenance-workspace.v1",
                              created_by="zf-maintenance", required=True)
