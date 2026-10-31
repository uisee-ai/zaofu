"""Read-only Git prerequisites shared by preparation, assembly and delivery."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml


def git_environment() -> dict[str, str]:
    return {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            "GIT_OPTIONAL_LOCKS": "0"}


def git_read(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], env=git_environment(),
                          capture_output=True, text=True, timeout=15, check=False)


def git_baseline_diagnostics(root: Path, *, require_root: bool = False) -> list[dict[str, Any]]:
    root = root.resolve()
    top = git_read(root, "rev-parse", "--show-toplevel")
    kind, message, fix = "", "", ""
    if top.returncode:
        kind, message = "git_repository_missing", "项目尚未建立 Git 仓库。"
        fix = "预览并批准 project-git-baseline 文件清单，建立命名分支和初始提交后重新准备。"
    elif require_root and Path(top.stdout.strip()).resolve() != root:
        kind, message = "git_project_root_mismatch", "项目目录不是 Git 仓库根目录。"
        fix = "明确项目与交付仓库边界，使用正确的项目根；不要隐式接管父仓库。"
    elif git_read(root, "symbolic-ref", "--quiet", "--short", "HEAD").returncode:
        kind, message = "git_detached_head", "交付仓库处于 detached HEAD。"
        fix = "切换到项目的命名分支后重新准备。"
    elif git_read(root, "rev-parse", "--verify", "HEAD^{commit}").returncode:
        kind, message = "git_head_missing", "Git 命名分支尚无首次提交。"
        fix = "预览并批准 project-git-baseline 文件清单，创建初始提交后重新准备。"
    if not kind:
        return []
    return [{"severity": "STOP", "kind": kind, "title": "工作流需要 Git 提交基线",
             "message": message, "fix_it": fix, "safe_auto_fix": False,
             "retryable": False}]


class GitBaselineError(ValueError):
    def __init__(self, blockers: list[dict[str, Any]]) -> None:
        self.blockers = blockers
        super().__init__("; ".join(f"{x['kind']}: {x['message']} {x['fix_it']}" for x in blockers))


def require_git_baseline(root: Path) -> None:
    if blockers := git_baseline_diagnostics(root):
        raise GitBaselineError(blockers)


def preparation_git_diagnostics(root: Path) -> list[dict[str, Any]]:
    """Check a declared registered controller before spending a provider turn.

    Other routes retain their own capability checks; in particular a read-only
    Workflow does not acquire a Git requirement merely by sharing this entry.
    Selected registered controllers recheck this invariant during assembly.
    """
    try:
        docs = list(yaml.safe_load_all((root / "zf.yaml").read_text()))
    except (OSError, ValueError, yaml.YAMLError):
        return []  # The config loader owns malformed-config diagnostics.
    registered = False
    for document in docs:
        value = document
        for key in ("metadata", "zaofu", "catalog", "assembly"):
            value = value.get(key) if isinstance(value, dict) else None
        registered |= value == "project-bound-v1"
    return git_baseline_diagnostics(root) if registered else []


def git_failure(error: BaseException) -> GitBaselineError | None:
    seen: set[int] = set()
    while id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, GitBaselineError):
            return error
        if error.__cause__ is None:
            return None
        error = error.__cause__
    return None
