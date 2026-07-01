#!/usr/bin/env python3
"""Diagnose multi-driver git worktree drift for ZaoFu.

This script is intentionally read-only. It reports the common failure shapes
from multi-driver work: dev checked out in the wrong worktree, dirty temporary
worktrees, hidden stash state, local/remote divergence, and runtime-like files
left untracked in the source tree.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


RUNTIME_LIKE_NAMES = {
    "latest-run.env",
    "latest-rescue-run.env",
    "scheduler.log",
    "scheduler.pid",
    "restore-sessions.sh",
    "session.list",
}
RUNTIME_LIKE_SUFFIXES = (".pid", ".env")
PROTECTED_BRANCHES = {"dev", "main", "master"}


@dataclass
class TmuxPaneInfo:
    session: str
    window: str
    pane: str
    pane_id: str
    cwd: str
    active: bool = False
    dead: bool = False


@dataclass
class WorktreeInfo:
    path: str
    head: str | None = None
    branch: str | None = None
    detached: bool = False
    bare: bool = False
    status_header: str = ""
    dirty_entries: list[str] = field(default_factory=list)
    untracked_runtime_like: list[str] = field(default_factory=list)
    ahead: int = 0
    behind: int = 0
    upstream: str | None = None
    tmux_panes: list[TmuxPaneInfo] = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.dirty_entries)


@dataclass
class DoctorReport:
    repo_root: str
    canonical_worktree: str
    worktrees: list[WorktreeInfo]
    warnings: list[str]
    stale_worktree_paths: list[str]
    merged_branch_candidates: list[str]
    stashes: list[str]
    tmux_available: bool = False
    tmux_unmatched_deleted_panes: list[TmuxPaneInfo] = field(default_factory=list)


def run_git(
    args: list[str],
    cwd: Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def repo_root(cwd: Path) -> Path:
    result = run_git(["rev-parse", "--show-toplevel"], cwd)
    return Path(result.stdout.strip()).resolve()


def parse_worktrees(output: str) -> list[WorktreeInfo]:
    worktrees: list[WorktreeInfo] = []
    current: WorktreeInfo | None = None
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("worktree "):
            if current is not None:
                worktrees.append(current)
            current = WorktreeInfo(path=line.removeprefix("worktree ").strip())
            continue
        if current is None:
            continue
        if line.startswith("HEAD "):
            current.head = line.removeprefix("HEAD ").strip()
        elif line.startswith("branch "):
            ref = line.removeprefix("branch ").strip()
            current.branch = ref.removeprefix("refs/heads/")
        elif line == "detached":
            current.detached = True
        elif line == "bare":
            current.bare = True
    if current is not None:
        worktrees.append(current)
    return worktrees


def parse_status_header(info: WorktreeInfo) -> None:
    header = info.status_header
    upstream_match = re.search(r"\.\.\.([^\s\]]+)", header)
    if upstream_match:
        info.upstream = upstream_match.group(1)
    ahead_match = re.search(r"ahead (\d+)", header)
    behind_match = re.search(r"behind (\d+)", header)
    if ahead_match:
        info.ahead = int(ahead_match.group(1))
    if behind_match:
        info.behind = int(behind_match.group(1))


def is_runtime_like(path: str) -> bool:
    name = Path(path).name
    if name in RUNTIME_LIKE_NAMES:
        return True
    return name.endswith(RUNTIME_LIKE_SUFFIXES)


def enrich_status(worktree: WorktreeInfo) -> None:
    path = Path(worktree.path)
    if not path.exists():
        return
    result = run_git(["status", "--short", "--branch"], path, check=False)
    lines = result.stdout.splitlines()
    if lines:
        worktree.status_header = lines[0]
        parse_status_header(worktree)
    for line in lines[1:]:
        if not line.strip():
            continue
        worktree.dirty_entries.append(line)
        if line.startswith("?? "):
            rel = line[3:].strip()
            if is_runtime_like(rel):
                worktree.untracked_runtime_like.append(rel)


def stale_worktree_paths(repo: Path) -> list[str]:
    result = run_git(["worktree", "list", "--porcelain"], repo, check=False)
    stale: list[str] = []
    current: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current = line.removeprefix("worktree ").strip()
        elif line == "prunable" and current:
            stale.append(current)
    return stale


def merged_branch_candidates(repo: Path, base_branch: str) -> list[str]:
    result = run_git(
        [
            "branch",
            "--merged",
            base_branch,
            "--format=%(refname:short)",
        ],
        repo,
        check=False,
    )
    if result.returncode != 0:
        return []
    branches = []
    for raw in result.stdout.splitlines():
        branch = raw.strip()
        if not branch or branch in PROTECTED_BRANCHES:
            continue
        if branch.startswith("backup/") or "backup" in branch:
            continue
        branches.append(branch)
    return sorted(branches)


def stash_list(repo: Path, limit: int) -> list[str]:
    result = run_git(["stash", "list"], repo, check=False)
    return result.stdout.splitlines()[:limit]


def run_tmux_list_panes() -> tuple[bool, list[TmuxPaneInfo]]:
    """Return all tmux panes with cwd information.

    tmux is an optional operator tool, so missing tmux or no tmux server is not
    an error for the doctor. The caller decides whether absence should warn.
    """

    separator = "\t"
    format_string = separator.join(
        [
            "#{session_name}",
            "#{window_index}",
            "#{pane_index}",
            "#{pane_id}",
            "#{pane_current_path}",
            "#{pane_active}",
            "#{pane_dead}",
        ]
    )
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", format_string],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        return False, []
    if result.returncode != 0:
        return False, []

    panes: list[TmuxPaneInfo] = []
    for raw in result.stdout.splitlines():
        parts = raw.split(separator)
        if len(parts) != 7:
            continue
        panes.append(
            TmuxPaneInfo(
                session=parts[0],
                window=parts[1],
                pane=parts[2],
                pane_id=parts[3],
                cwd=parts[4],
                active=parts[5] == "1",
                dead=parts[6] == "1",
            )
        )
    return True, panes


def path_contains(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
    except ValueError:
        return False
    return True


def attach_tmux_panes(
    worktrees: list[WorktreeInfo],
    warnings: list[str],
    *,
    require_tmux: bool,
) -> tuple[bool, list[TmuxPaneInfo]]:
    available, panes = run_tmux_list_panes()
    if not available:
        if require_tmux:
            warnings.append("tmux 不可用或没有运行中的 tmux server。")
        return False, []

    worktree_paths = [
        (worktree, Path(worktree.path).resolve())
        for worktree in worktrees
        if Path(worktree.path).exists()
    ]
    unmatched_deleted: list[TmuxPaneInfo] = []
    sessions_to_worktrees: dict[str, set[str]] = {}

    for pane in panes:
        if "(deleted)" in pane.cwd:
            unmatched_deleted.append(pane)
            warnings.append(
                f"tmux session {pane.session} pane {pane.pane_id} cwd 已删除: {pane.cwd}"
            )
            continue

        pane_path = Path(pane.cwd).expanduser().resolve()
        matched = False
        for worktree, worktree_path in worktree_paths:
            if not path_contains(worktree_path, pane_path):
                continue
            worktree.tmux_panes.append(pane)
            sessions_to_worktrees.setdefault(pane.session, set()).add(worktree.path)
            matched = True
            break
        if matched:
            continue

    for session, paths in sorted(sessions_to_worktrees.items()):
        if len(paths) <= 1:
            continue
        warnings.append(
            f"tmux session {session} 同时关联多个 worktree: {', '.join(sorted(paths))}"
        )

    return True, unmatched_deleted


def build_report(
    cwd: Path,
    canonical: Path | None,
    base_branch: str,
    stash_limit: int,
    *,
    include_tmux: bool,
    require_tmux: bool,
) -> DoctorReport:
    root = repo_root(cwd)
    result = run_git(["worktree", "list", "--porcelain"], root)
    worktrees = parse_worktrees(result.stdout)
    for worktree in worktrees:
        enrich_status(worktree)

    canonical_path = (canonical or (Path(worktrees[0].path) if worktrees else root)).resolve()
    warnings: list[str] = []

    dev_holders = [w for w in worktrees if w.branch == base_branch]
    if not dev_holders:
        warnings.append(f"没有 worktree checkout {base_branch}; merge/push 前需要明确 owner。")
    elif len(dev_holders) > 1:
        paths = ", ".join(w.path for w in dev_holders)
        warnings.append(f"{base_branch} 被多个 worktree 持有: {paths}")
    else:
        dev_path = Path(dev_holders[0].path).resolve()
        if dev_path != canonical_path:
            warnings.append(
                f"{base_branch} 当前在 {dev_path}, 不在 canonical worktree {canonical_path}。"
            )

    canonical_info = next(
        (w for w in worktrees if Path(w.path).resolve() == canonical_path),
        None,
    )
    if canonical_info and canonical_info.branch != base_branch:
        warnings.append(
            f"canonical worktree 当前分支是 {canonical_info.branch}, 不是 {base_branch}。"
        )

    for worktree in worktrees:
        label = f"{worktree.path} ({worktree.branch or 'detached'})"
        if worktree.dirty:
            warnings.append(f"{label} 有未提交/未跟踪变更: {len(worktree.dirty_entries)} 条。")
        if worktree.behind:
            warnings.append(f"{label} 落后 upstream {worktree.behind} 个提交。")
        if worktree.ahead and worktree.branch == base_branch:
            warnings.append(f"{label} 领先 upstream {worktree.ahead} 个提交, push/merge 前需确认。")
        if worktree.untracked_runtime_like:
            warnings.append(
                f"{label} 有 source tree runtime-like 未跟踪文件: "
                + ", ".join(worktree.untracked_runtime_like)
            )

    stale = stale_worktree_paths(root)
    for path in stale:
        warnings.append(f"发现 prunable/stale worktree 记录: {path}")

    stashes = stash_list(root, stash_limit)
    if stashes:
        warnings.append(f"存在 stash {len(stashes)} 条展示项; 删除分支/清理前请确认是否仍需恢复。")

    tmux_available = False
    tmux_unmatched_deleted_panes: list[TmuxPaneInfo] = []
    if include_tmux:
        tmux_available, tmux_unmatched_deleted_panes = attach_tmux_panes(
            worktrees,
            warnings,
            require_tmux=require_tmux,
        )

    return DoctorReport(
        repo_root=str(root),
        canonical_worktree=str(canonical_path),
        worktrees=worktrees,
        warnings=warnings,
        stale_worktree_paths=stale,
        merged_branch_candidates=merged_branch_candidates(root, base_branch),
        stashes=stashes,
        tmux_available=tmux_available,
        tmux_unmatched_deleted_panes=tmux_unmatched_deleted_panes,
    )


def print_human(report: DoctorReport) -> None:
    print(f"Repo: {report.repo_root}")
    print(f"Canonical worktree: {report.canonical_worktree}")
    print()
    print("Worktrees:")
    for worktree in report.worktrees:
        marker = "dirty" if worktree.dirty else "clean"
        branch = worktree.branch or ("detached" if worktree.detached else "unknown")
        upstream = f" -> {worktree.upstream}" if worktree.upstream else ""
        divergence = []
        if worktree.ahead:
            divergence.append(f"ahead {worktree.ahead}")
        if worktree.behind:
            divergence.append(f"behind {worktree.behind}")
        divergence_text = f" [{', '.join(divergence)}]" if divergence else ""
        print(f"- {worktree.path}")
        print(f"  branch: {branch}{upstream}{divergence_text}")
        print(f"  head: {worktree.head or '-'}")
        print(f"  status: {marker}")
        if worktree.tmux_panes:
            sessions = sorted({pane.session for pane in worktree.tmux_panes})
            print(
                f"  tmux: {len(worktree.tmux_panes)} pane(s), "
                f"session(s): {', '.join(sessions)}"
            )
        if worktree.untracked_runtime_like:
            print(f"  runtime-like untracked: {', '.join(worktree.untracked_runtime_like)}")
    print()

    if report.tmux_available and report.tmux_unmatched_deleted_panes:
        print("Deleted-cwd tmux panes:")
        for pane in report.tmux_unmatched_deleted_panes:
            print(
                f"- {pane.session}:{pane.window}.{pane.pane} "
                f"{pane.pane_id} cwd={pane.cwd}"
            )
        print()

    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(f"- {warning}")
    else:
        print("Warnings: none")

    if report.merged_branch_candidates:
        print()
        print("Merged branch cleanup candidates:")
        for branch in report.merged_branch_candidates:
            print(f"- {branch}")

    if report.stashes:
        print()
        print("Recent stashes:")
        for item in report.stashes:
            print(f"- {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only diagnostics for ZaoFu multi-driver git worktrees.",
    )
    parser.add_argument(
        "--canonical",
        type=Path,
        help="Expected canonical dev worktree path. Defaults to the main worktree.",
    )
    parser.add_argument(
        "--base-branch",
        default="dev",
        help="Canonical integration branch. Defaults to dev.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when warnings are present.",
    )
    parser.add_argument(
        "--stash-limit",
        type=int,
        default=8,
        help="Number of recent stash entries to display.",
    )
    parser.add_argument(
        "--no-tmux",
        action="store_true",
        help="Do not scan tmux panes for worktree association.",
    )
    parser.add_argument(
        "--require-tmux",
        action="store_true",
        help="Warn when tmux is unavailable or has no running server.",
    )
    args = parser.parse_args(argv)

    report = build_report(
        cwd=Path.cwd(),
        canonical=args.canonical,
        base_branch=args.base_branch,
        stash_limit=max(args.stash_limit, 0),
        include_tmux=not args.no_tmux,
        require_tmux=args.require_tmux,
    )
    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print_human(report)

    if args.strict and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        else:
            print(f"git command failed: {exc.cmd}", file=sys.stderr)
        raise SystemExit(exc.returncode)
    except KeyboardInterrupt:
        raise SystemExit(130)
