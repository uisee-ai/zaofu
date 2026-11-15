"""Provision gitignored runtime env (venv, node_modules) into git worktrees.

Git worktrees share `.git` but not working-tree files; gitignored build
artifacts (`.venv`, `node_modules`) present in the main checkout are absent in
fresh worktrees. Gate/verify commands that need them (run_tests.sh, tsc, npm)
then fail with "no virtualenv found" / "tsc not found" — not a worker defect,
just an unprovisioned environment.

Symlinking each declared non-Python env path from the source root into the
worktree lets those commands run natively. For Python `.venv` with
`bootstrap_uv_dev=True`, Zaofu prefers a worktree-local `uv sync --extra dev`
so editable installs point at the worker's current `src/` tree. The targets
are gitignored, so they never show up in `git status`/`diff` and cannot pollute
a commit. A declared path that cannot be provisioned is skipped — that surfaces
later as a normal gate failure, not a crash here.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.environment_preparation import declared_setup_identity, environment_preparation_dir
from zf.runtime.environment_usage import EnvironmentInUse, environment_in_use, environment_mutation
from zf.runtime.environment_resources import register_environment_resources
from zf.runtime.environment_readiness import (
    setup_environment_matches, invalidate_setup_environment, record_setup_environment,
)


Runner = Callable[..., subprocess.CompletedProcess[str]]

# project.scripts.setup 的幂等标记(内容 = 脚本 sha256)。worktree 本地、
# 未跟踪;脚本变更即 digest 不匹配 → 重跑。
SETUP_MARKER = ".zf-setup.done"

_SETUP_INPUT_NAMES = {
    ".node-version",
    ".python-version",
    ".tool-versions",
    "Cargo.lock",
    "Cargo.toml",
    "Gemfile",
    "Gemfile.lock",
    "bun.lock",
    "bun.lockb",
    "composer.json",
    "composer.lock",
    "go.mod",
    "go.sum",
    "package-lock.json",
    "package.json",
    "pdm.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
}

_SETUP_SCAN_EXCLUDES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".zf",
    "build",
    "dist",
    "node_modules",
}


@dataclass(frozen=True)
class ProjectSetupResult:
    ran: bool
    ok: bool
    exit_code: int | None = None
    detail: str = ""


def _setup_digest(worktree: Path, script: str, setup_inputs: list[str] | None = None) -> str:
    """Bind setup readiness to the script and tracked dependency surfaces.

    A newly scaffolded project can add its package manifests after role
    worktrees were minted. Script-only markers would keep the earlier no-op
    result forever, leaving later verifier checkouts without dependencies.
    """
    digest = hashlib.sha256()
    digest.update(script.encode("utf-8"))
    if setup_inputs:
        digest.update(declared_setup_identity(worktree, setup_inputs).encode())
    inputs: list[Path] = []
    try:
        for root, dirs, files in os.walk(worktree):
            dirs[:] = [name for name in dirs if name not in _SETUP_SCAN_EXCLUDES]
            root_path = Path(root)
            inputs.extend(root_path / name for name in files if name in _SETUP_INPUT_NAMES)
    except OSError:
        inputs = []
    for path in sorted(inputs, key=lambda item: item.relative_to(worktree).as_posix()):
        relative = path.relative_to(worktree).as_posix()
        digest.update(b"\0path\0")
        digest.update(relative.encode("utf-8"))
        try:
            digest.update(b"\0body\0")
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"\0unreadable\0")
    return digest.hexdigest()


def run_project_setup(
    worktree: Path,
    script: str,
    *,
    marker_dir: Path | None = None,
    timeout_s: int = 600,
    runner: Runner | None = None,
    force: bool = False,
    setup_inputs: list[str] | None = None,
    required_paths: list[str] | None = None,
) -> ProjectSetupResult:
    """Execute the project-declared setup script inside a fresh worktree.

    项目在 `project.scripts.setup` 声明如何把 checkout 变成可运行状态
    (pnpm install / uv sync ...),宿主在 worktree 铸造时执行。与
    `_bootstrap_uv_dev_env`(无声明时的 Python fallback)不同,声明了
    setup 即为项目合约,失败必须上浮给调用方 fail-closed,不能静默降级。
    """
    script = (script or "").strip()
    try:
        register_environment_resources(worktree, required_paths or [])
    except (OSError, ValueError, TimeoutError) as exc:
        return ProjectSetupResult(ran=False, ok=False, detail=f'environment declaration unavailable: {exc}')
    if not script:
        return ProjectSetupResult(ran=False, ok=True, detail="no setup declared")
    marker = (marker_dir or worktree) / SETUP_MARKER
    try:
        if not force:
            # Read-only reuse may coexist with workers; mutation still needs
            # the exclusive lock and rechecks inputs after acquiring it.
            with environment_in_use(worktree):
                try:
                    if (marker.read_text(encoding="utf-8").strip() == _setup_digest(worktree, script, setup_inputs)
                            and setup_environment_matches(worktree, marker)):
                        return ProjectSetupResult(ran=False, ok=True, detail="setup marker matches")
                except (OSError, ValueError):
                    pass
        with environment_mutation(worktree):
            with locked_path(marker, timeout_seconds=0):
                return _run_locked_setup(worktree, script, marker, timeout_s, runner, force, setup_inputs)
    except (TimeoutError, EnvironmentInUse):
        return ProjectSetupResult(ran=False, ok=False, detail="environment in use or setup already in progress; retry after holders exit")
    except OSError as exc:
        return ProjectSetupResult(ran=False, ok=False, detail=f"setup lock unavailable: {exc}")


def _run_locked_setup(
    worktree: Path, script: str, marker: Path, timeout_s: int,
    runner: Runner | None, force: bool, setup_inputs: list[str] | None,
) -> ProjectSetupResult:
    try:
        digest = _setup_digest(worktree, script, setup_inputs)
    except (ValueError, OSError) as exc:
        marker.unlink(missing_ok=True)
        return ProjectSetupResult(ran=False, ok=False, detail=str(exc))
    try:
        if (
            not force
            and marker.exists()
            and marker.read_text(encoding="utf-8").strip() == digest
            and setup_environment_matches(worktree, marker)
        ):
            return ProjectSetupResult(ran=False, ok=True, detail="setup marker matches")
    except OSError:
        pass
    # An interrupted rebuild must not leave a previous readiness claim usable.
    try:
        marker.unlink(missing_ok=True)
        invalidate_setup_environment(worktree, marker)
    except OSError as exc:
        return ProjectSetupResult(ran=False, ok=False, detail=f"cannot invalidate setup readiness: {exc}")
    run = runner or subprocess.run
    try:
        result = run(
            ["bash", "-c", script],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return ProjectSetupResult(ran=True, ok=False, detail=f"setup did not run: {exc}")
    if result.returncode != 0:
        tail = ((result.stderr or "") + (result.stdout or ""))[-400:]
        return ProjectSetupResult(
            ran=True, ok=False, exit_code=result.returncode, detail=tail,
        )
    try:
        current_digest = _setup_digest(worktree, script, setup_inputs)
    except (ValueError, OSError) as exc:
        return ProjectSetupResult(ran=True, ok=False, exit_code=0, detail=str(exc))
    if current_digest != digest:
        return ProjectSetupResult(ran=True, ok=False, exit_code=0,
                                  detail="setup inputs changed during preparation; readiness not published")
    try:
        record_setup_environment(worktree, marker)
        atomic_write_text(marker, digest + "\n")
    except OSError as exc:
        return ProjectSetupResult(ran=True, ok=False, exit_code=0,
                                  detail=f"setup readiness could not be persisted: {exc}")
    return ProjectSetupResult(ran=True, ok=True, exit_code=0)


def provision_worktree_env(
    worktree: Path,
    source_root: Path,
    paths: list[str],
    *,
    bootstrap_uv_dev: bool = False,
    runner: Runner | None = None,
) -> list[str]:
    """Symlink each existing ``source_root/<path>`` into ``worktree/<path>``.

    Best-effort and idempotent: skips paths whose source is missing or whose
    destination already exists. Returns the relative paths provisioned.
    """
    if not paths:
        return []
    register_environment_resources(worktree, paths)
    if source_root.exists():
        register_environment_resources(source_root, paths)
    try:
        with environment_mutation(worktree):
            return _provision_locked(worktree, source_root, paths, bootstrap_uv_dev=bootstrap_uv_dev, runner=runner)
    except TimeoutError as busy:
        # A worker may hold the environment while another role reuses it.
        # Prove that no mutation is needed; unknown readiness still waits.
        try:
            with environment_in_use(worktree):
                reused = _reuse_provisioned_env(worktree, source_root, paths, bootstrap_uv_dev)
        except EnvironmentInUse:
            raise busy
        if reused is not None:
            return reused
        raise


def _reuse_provisioned_env(
    worktree: Path, source_root: Path, paths: list[str], bootstrap_uv_dev: bool,
) -> list[str] | None:
    reused: list[str] = []
    for raw in paths:
        relative = str(raw).strip().strip("/")
        if not relative:
            continue
        if relative == ".venv" and bootstrap_uv_dev and (worktree / "pyproject.toml").exists():
            try:
                receipt = json.loads((environment_preparation_dir(worktree) / "uv-bootstrap.json").read_text())
                identity = _local_venv_identity(worktree)
                if (not isinstance(receipt, dict) or receipt.get("status") != "ready" or not identity
                        or receipt.get("environment") != identity
                        or receipt.get("digest") != _setup_digest(worktree, "uv sync --extra dev; fallback uv sync")):
                    return None
            except (OSError, ValueError):
                return None
            reused.append(relative)
        elif (source_root / relative).exists():
            target = worktree / relative
            if not target.exists() and not target.is_symlink():
                return None
    return reused


def _provision_locked(worktree: Path, source_root: Path, paths: list[str], *,
                      bootstrap_uv_dev: bool, runner: Runner | None) -> list[str]:
    normalized_paths = [
        str(raw).strip().strip("/")
        for raw in (paths or [])
        if str(raw).strip().strip("/")
    ]
    bootstrapped: set[str] = set()
    local_python_required = bootstrap_uv_dev and (worktree / "pyproject.toml").exists()
    if bootstrap_uv_dev and ".venv" in normalized_paths:
        if _bootstrap_uv_dev_env(worktree, runner=runner):
            bootstrapped.add(".venv")

    provisioned: list[str] = []
    for rel in normalized_paths:
        src = source_root / rel
        dst = worktree / rel
        if rel in bootstrapped:
            provisioned.append(rel)
            continue
        if rel == ".venv" and local_python_required:
            continue  # A failed local bootstrap must not inherit another checkout's editable install.
        if not src.exists():
            continue
        if dst.exists() or dst.is_symlink():
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(src.resolve(), target_is_directory=src.is_dir())
        except OSError:
            continue
        provisioned.append(rel)
    return provisioned


def _bootstrap_uv_dev_env(project_root: Path, *, runner: Runner | None = None) -> bool:
    """Best-effort worktree-local bootstrap for verification.

    Product-standard E2E uses fresh `/tmp` worktrees where `.venv` is absent
    before the first worker runs. When `.venv` is explicitly listed in
    `runtime.workdirs.provision_paths`, create a worktree-local env so editable
    installs point at that worktree, including newly added `src/` modules.
    Failure is intentionally non-fatal; the gate command will surface the real
    dependency issue later.
    """
    if not (project_root / "pyproject.toml").exists():
        return False
    receipt = environment_preparation_dir(project_root) / "uv-bootstrap.json"
    digest = _setup_digest(project_root, "uv sync --extra dev; fallback uv sync")
    previous = {}
    if receipt.exists():
        try:
            previous = json.loads(receipt.read_text())
        except (OSError, ValueError):
            return False  # Unknown preparation evidence never becomes ready by existence.
        if not isinstance(previous, dict):
            return False
    if (project_root / ".venv").exists():
        if (previous.get("status") == "ready" and previous.get("digest") == digest
                and previous.get("environment") and previous["environment"] == _local_venv_identity(project_root)):
            return True
        if previous.get("status") not in {"preparing", "failed"}:
            return False  # Preserve externally managed/in-use environments; no implicit rebuild.
    atomic_write_text(receipt, json.dumps({"status": "preparing", "digest": digest}) + "\n")
    run = runner or subprocess.run
    attempts = (
        ["uv", "sync", "--extra", "dev"],
        ["uv", "sync"],
    )
    for argv in attempts:
        try:
            result = run(
                argv,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            atomic_write_text(receipt, json.dumps({"status": "failed", "digest": digest}) + "\n")
            return False
        environment = _local_venv_identity(project_root)
        if (result.returncode == 0 and environment
                and _setup_digest(project_root, "uv sync --extra dev; fallback uv sync") == digest):
            atomic_write_text(receipt, json.dumps({"status": "ready", "digest": digest,
                "command": argv, "environment": environment}) + "\n")
            return True
    atomic_write_text(receipt, json.dumps({"status": "failed", "digest": digest}) + "\n")
    return False


def _local_venv_identity(project_root: Path) -> dict[str, object] | None:
    path = project_root / ".venv"
    if path.is_symlink() or not path.is_dir():
        return None
    stat = path.stat()
    return {"path": str(path.resolve()), "device": stat.st_dev, "inode": stat.st_ino}
