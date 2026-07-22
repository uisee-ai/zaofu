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
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


Runner = Callable[..., subprocess.CompletedProcess[str]]

# project.scripts.setup 的幂等标记(内容 = 脚本 sha256)。worktree 本地、
# 未跟踪;脚本变更即 digest 不匹配 → 重跑。
SETUP_MARKER = ".zf-setup.done"

_SETUP_INPUT_NAMES = {
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


def _setup_digest(worktree: Path, script: str) -> str:
    """Bind setup readiness to the script and tracked dependency surfaces.

    A newly scaffolded project can add its package manifests after role
    worktrees were minted. Script-only markers would keep the earlier no-op
    result forever, leaving later verifier checkouts without dependencies.
    """
    digest = hashlib.sha256()
    digest.update(script.encode("utf-8"))
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
) -> ProjectSetupResult:
    """Execute the project-declared setup script inside a fresh worktree.

    项目在 `project.scripts.setup` 声明如何把 checkout 变成可运行状态
    (pnpm install / uv sync ...),宿主在 worktree 铸造时执行。与
    `_bootstrap_uv_dev_env`(无声明时的 Python fallback)不同,声明了
    setup 即为项目合约,失败必须上浮给调用方 fail-closed,不能静默降级。
    """
    script = (script or "").strip()
    if not script:
        return ProjectSetupResult(ran=False, ok=True, detail="no setup declared")
    digest = _setup_digest(worktree, script)
    marker = (marker_dir or worktree) / SETUP_MARKER
    try:
        if (
            not force
            and marker.exists()
            and marker.read_text(encoding="utf-8").strip() == digest
        ):
            return ProjectSetupResult(ran=False, ok=True, detail="setup marker matches")
    except OSError:
        pass
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
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(digest + "\n", encoding="utf-8")
    except OSError:
        pass
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
    normalized_paths = [
        str(raw).strip().strip("/")
        for raw in (paths or [])
        if str(raw).strip().strip("/")
    ]
    bootstrapped: set[str] = set()
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
    if (project_root / ".venv").exists():
        return True
    if not (project_root / "pyproject.toml").exists():
        return False
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
            return False
        if result.returncode == 0 or (project_root / ".venv").exists():
            return True
    return False
