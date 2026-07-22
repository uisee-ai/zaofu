"""Capture git state for a project root via subprocess.

Pure side-effect layer — kept out of core/ per the deterministic kernel rule.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from zf.core.state.git_state import GitState


_HARNESS_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "ZaoFu Harness",
    "GIT_AUTHOR_EMAIL": "harness@zaofu.local",
    "GIT_COMMITTER_NAME": "ZaoFu Harness",
    "GIT_COMMITTER_EMAIL": "harness@zaofu.local",
}
_RUNTIME_DIR_PREFIXES = (".zf-", ".zf.")
_RUNTIME_LOG_PATTERNS = (
    "autoresearch-resident-*.log",
    "smoke-start-*.log",
    "start-r*.log",
    "webkanban-*.log",
)


def git_env(*, allow_large_commit: bool = False) -> dict[str, str]:
    """Environment for harness-issued git subprocesses.

    Candidate-integration and ship commits/cherry-picks are machine-generated;
    a deterministic harness identity is injected so they never fail with
    "Author identity unknown" on a host/worktree without a configured git user
    (fresh CI box, container, isolated E2E repo). Real bug: the calc / cj-mono
    full-flow candidate.integration aborted — mislabeled candidate.conflict —
    because ``git commit`` in the candidate worktree had no identity.
    """
    env = {**os.environ, **_HARNESS_GIT_IDENTITY}
    if allow_large_commit:
        env["ZF_ALLOW_LARGE_COMMIT"] = "1"
    return env


@dataclass
class GitDiffContext:
    base_sha: str = ""
    branch: str | None = None
    head: str | None = None
    last_commit_msg: str = ""
    commits: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    dirty_files: list[str] = field(default_factory=list)
    diff_stat: str = ""
    diff_hash: str = ""
    ts: str = ""


def _git(project_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        raise _GitError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


class _GitError(Exception):
    pass


def _expand_dirty_path(project_root: Path, status: str, path: str) -> list[str]:
    path = path.strip()
    if not path:
        return []
    if is_runtime_generated_path(path.rstrip("/")):
        return []
    if status == "??" and path.endswith("/"):
        try:
            out = _git(
                project_root,
                "ls-files",
                "--others",
                "--exclude-standard",
                "--",
                path,
            )
        except (_GitError, FileNotFoundError, subprocess.TimeoutExpired):
            return [path.rstrip("/")]
        files = [line.strip() for line in out.splitlines() if line.strip()]
        return filter_runtime_generated_paths(files or [path.rstrip("/")])
    return [path]


def is_runtime_generated_path(path: str) -> bool:
    """Return True for harness runtime state/log paths that are not source."""
    normalized = str(path or "").strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return False
    first = normalized.split("/", 1)[0]
    if first == ".zf" or first.startswith(_RUNTIME_DIR_PREFIXES):
        return True
    return any(fnmatch.fnmatch(first, pattern) for pattern in _RUNTIME_LOG_PATTERNS)


def filter_runtime_generated_paths(paths: list[str]) -> list[str]:
    return [
        path for path in paths
        if not is_runtime_generated_path(path)
    ]


def capture_git_state(project_root: Path | str) -> GitState:
    project_root = Path(project_root)
    ts = datetime.now(timezone.utc).isoformat()
    try:
        branch = _git(project_root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        head = _git(project_root, "rev-parse", "HEAD").strip()
        last_commit = _git(project_root, "log", "-1", "--pretty=%s").strip()
        porcelain = _git(project_root, "status", "--porcelain")
    except (_GitError, FileNotFoundError, subprocess.TimeoutExpired):
        return GitState(ts=ts)

    dirty: list[str] = []
    for line in porcelain.splitlines():
        # porcelain format: "XY path"
        if len(line) > 3:
            dirty.extend(_expand_dirty_path(project_root, line[:2], line[3:]))
    dirty = filter_runtime_generated_paths(dirty)
    return GitState(
        branch=branch or None,
        head=head or None,
        dirty_files=dirty,
        last_commit_msg=last_commit,
        ts=ts,
    )


def capture_commits_since(project_root: Path | str, since_sha: str) -> list[str]:
    project_root = Path(project_root)
    try:
        out = _git(project_root, "log", "--format=%H", f"{since_sha}..HEAD")
    except (_GitError, FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def capture_commit_summaries_since(
    project_root: Path | str,
    since_sha: str,
    *,
    max_commits: int = 20,
) -> list[str]:
    if not since_sha or max_commits <= 0:
        return []
    project_root = Path(project_root)
    try:
        out = _git(
            project_root,
            "log",
            f"--max-count={max_commits}",
            "--format=%H %s",
            f"{since_sha}..HEAD",
        )
    except (_GitError, FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def capture_files_changed(project_root: Path | str, since_sha: str) -> list[str]:
    project_root = Path(project_root)
    try:
        out = _git(project_root, "diff", "--name-only", since_sha, "HEAD")
    except (_GitError, FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def capture_files_touched_since(project_root: Path | str, since_sha: str = "") -> list[str]:
    project_root = Path(project_root)
    files: list[str] = []
    if since_sha:
        files.extend(capture_files_changed(project_root, since_sha))
        try:
            out = _git(project_root, "diff", "--name-only", since_sha)
            files.extend(line.strip() for line in out.splitlines() if line.strip())
        except (_GitError, FileNotFoundError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            out = _git(project_root, "diff", "--name-only", "HEAD")
            files.extend(line.strip() for line in out.splitlines() if line.strip())
        except (_GitError, FileNotFoundError, subprocess.TimeoutExpired):
            pass
    files.extend(capture_git_state(project_root).dirty_files)
    return _unique(filter_runtime_generated_paths(files))


def capture_diff_stat(project_root: Path | str, since_sha: str = "") -> str:
    project_root = Path(project_root)
    args = ("diff", "--stat", "--no-ext-diff", since_sha or "HEAD")
    try:
        return _git(project_root, *args).strip()
    except (_GitError, FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def capture_diff_hash(project_root: Path | str, since_sha: str = "") -> str:
    project_root = Path(project_root)
    args = ("diff", "--no-ext-diff", since_sha or "HEAD")
    try:
        out = _git(project_root, *args)
    except (_GitError, FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if not out:
        return ""
    return hashlib.sha256(out.encode("utf-8")).hexdigest()


def capture_git_diff_context(
    project_root: Path | str,
    *,
    base_sha: str = "",
    max_commits: int = 20,
) -> GitDiffContext:
    base_sha = base_sha.strip()
    state = capture_git_state(project_root)
    return GitDiffContext(
        base_sha=base_sha,
        branch=state.branch,
        head=state.head,
        last_commit_msg=state.last_commit_msg,
        commits=capture_commit_summaries_since(
            project_root, base_sha, max_commits=max_commits,
        ),
        files_touched=capture_files_touched_since(project_root, base_sha),
        dirty_files=list(state.dirty_files),
        diff_stat=capture_diff_stat(project_root, base_sha),
        diff_hash=capture_diff_hash(project_root, base_sha),
        ts=state.ts,
    )


def render_git_diff_context(
    context: GitDiffContext | None,
    *,
    max_files: int = 30,
    max_commits: int = 20,
    max_stat_lines: int = 20,
) -> str:
    if context is None or not (
        context.branch
        or context.head
        or context.base_sha
        or context.files_touched
        or context.dirty_files
    ):
        return "_(no git context captured)_"

    lines: list[str] = []
    if context.branch:
        lines.append(f"- **Branch**: `{context.branch}`")
    if context.head:
        lines.append(f"- **HEAD**: `{context.head}`")
    if context.base_sha:
        lines.append(f"- **Base**: `{context.base_sha}`")
    if context.last_commit_msg:
        lines.append(f"- **Last commit**: {context.last_commit_msg}")

    if context.commits:
        lines.append("- **Commits since base**:")
        for item in context.commits[:max_commits]:
            sha, _, msg = item.partition(" ")
            suffix = f" {msg}" if msg else ""
            lines.append(f"  - `{sha[:12]}`{suffix}")
        if len(context.commits) > max_commits:
            lines.append(f"  - ... {len(context.commits) - max_commits} more")
    elif context.base_sha:
        lines.append("- **Commits since base**: none")

    if context.files_touched:
        files = context.files_touched[:max_files]
        lines.append(f"- **Files touched**: {', '.join(files)}")
        if len(context.files_touched) > max_files:
            lines.append(f"- **Files touched truncated**: {len(context.files_touched) - max_files} more")
    else:
        lines.append("- **Files touched**: none")

    if context.dirty_files:
        dirty = context.dirty_files[:max_files]
        lines.append(f"- **Dirty files**: {', '.join(dirty)}")
        if len(context.dirty_files) > max_files:
            lines.append(f"- **Dirty files truncated**: {len(context.dirty_files) - max_files} more")
    else:
        lines.append("- **Working tree**: clean")

    if context.diff_stat:
        stat_lines = context.diff_stat.splitlines()
        if context.diff_hash:
            lines.append(f"- **Diff hash**: `{context.diff_hash}`")
        lines.append("- **Diff stat**:")
        lines.append("```")
        lines.extend(stat_lines[:max_stat_lines])
        if len(stat_lines) > max_stat_lines:
            lines.append(f"... {len(stat_lines) - max_stat_lines} more lines")
        lines.append("```")
    elif context.base_sha:
        lines.append("- **Diff stat**: empty")

    return "\n".join(lines)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out
