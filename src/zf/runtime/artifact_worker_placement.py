"""Start an explicitly managed Codex worker in its own transient user scope."""

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess


def _unit_name(project_root: Path, state_dir: Path, actor: str) -> str:
    identity = b"\0".join(
        (
            os.fsencode(project_root.resolve()),
            os.fsencode((project_root / state_dir).resolve()),
            actor.encode(),
        )
    )
    return "zf-worker-" + hashlib.sha256(identity).hexdigest()[:24] + ".scope"


def _app_slice() -> str:
    result = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            "app.slice",
            "--property=ControlGroup",
            "--value",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=3,
    )
    group = result.stdout.strip()
    if not group.startswith("/") or not group.endswith("/app.slice"):
        raise ValueError("a user systemd app.slice with a ControlGroup is required")
    return group


def managed_worker_cgroup(project_root: Path, state_dir: Path, actor: str) -> str:
    """Resolve the exact cgroup to declare for this project's managed worker."""
    return f"{_app_slice()}/{_unit_name(project_root, state_dir, actor)}"


def worker_scope_prefix(
    project_root: Path,
    state_dir: Path,
    actor: str,
    group: str,
) -> list[str]:
    unit = PurePosixPath(group).name
    if not (unit.startswith("zf-worker-") and unit.endswith(".scope")):
        # The deployment handles placement in this externally managed group.
        return []
    if unit != _unit_name(project_root, state_dir, actor):
        raise ValueError(
            "managed worker scope does not match this project, state directory and instance"
        )
    binary = shutil.which("systemd-run")
    try:
        expected = managed_worker_cgroup(project_root, state_dir, actor)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []  # Dispatch can still use the authenticated CLI on this host.
    if group != expected:
        raise ValueError("managed worker scope is outside the user's app.slice")
    if not binary:
        return []
    if (Path("/sys/fs/cgroup") / group.lstrip("/")).is_dir():
        raise RuntimeError(f"managed worker scope is already occupied: {unit}")
    # StartTransientUnit atomically refuses an existing scope, including a second
    # launch racing the first. Never attach a second provider to a live unit.
    return [
        binary,
        "--user",
        "--scope",
        "--collect",
        "--quiet",
        "--slice=app.slice",
        "--unit=" + unit,
        "--",
    ]
