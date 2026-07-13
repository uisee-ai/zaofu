"""zf stop — stop the harness loop."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.runtime.transport import make_transport, TmuxTransport
from zf.runtime.shutdown import GracefulShutdown
from zf.runtime.tmux import TmuxSession


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("stop", help="Stop the harness loop")
    parser.add_argument("--force", action="store_true", help="Force kill without graceful shutdown")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast scoped teardown: requeue stale WIP, emit run.teardown, skip snapshots",
    )
    parser.add_argument(
        "--preserve-run-manager",
        action="store_true",
        help="Preserve a dedicated resident Run Manager tmux session across stop",
    )
    parser.add_argument(
        "--include-run-manager",
        action="store_true",
        help="Also stop the dedicated resident Run Manager session",
    )
    parser.add_argument(
        "--clean-workdirs",
        action="store_true",
        help=(
            "After stopping, remove this state dir's git worktrees and the "
            "worker branches they held (stale ones block the next flow that "
            "shares the product repo)"
        ),
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        ctx = resolve_project_context()
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    config_path = ctx.config_path
    state_dir = ctx.state_dir

    if not state_dir.exists():
        print(
            f"Error: state dir {state_dir} not found. To fix: run 'zf init'",
            file=sys.stderr,
        )
        return 1

    # Tolerate missing/broken config — fall back to default tmux session.
    transport = None
    config = ctx.config
    session_name = "zf"
    if config is not None:
        try:
            session_name = config.session.tmux_session
            transport = make_transport(config)
        except ConfigError:
            transport = None
    if transport is None:
        transport = TmuxTransport(TmuxSession(session_name=session_name))

    preserve_run_manager = _should_preserve_run_manager(config, args)

    if getattr(args, "force", False):
        if not getattr(args, "preserve_run_manager", False):
            preserve_run_manager = False
        if preserve_run_manager:
            _write_preserve_marker_for_force(config, state_dir)
        excluded = _preserved_run_manager_roles(config) if preserve_run_manager else set()
        transport.shutdown(exclude_roles=excluded)
        try:
            from zf.runtime.autoresearch_resident_sidecar import (
                stop_autoresearch_resident_sidecar_by_pidfile,
            )

            stop_autoresearch_resident_sidecar_by_pidfile(state_dir)
        except Exception:
            pass
        lock_path = state_dir / "loop.lock"
        lock_path.unlink(missing_ok=True)
        print(f"Force-stopped harness session: {session_name}")
        _maybe_clean_workdirs(args, config_path.parent, state_dir)
        return 0

    shutdown = GracefulShutdown(
        state_dir,
        transport,
        config=config,
        preserve_run_manager=preserve_run_manager,
    )
    if getattr(args, "fast", False):
        steps = shutdown.execute_fast()
        print(f"Fast-stopped harness session: {session_name} ({len(steps)} steps completed)")
        _maybe_clean_workdirs(args, config_path.parent, state_dir)
        return 0
    steps = shutdown.execute()
    print(f"Stopped harness session: {session_name} ({len(steps)} steps completed)")
    _maybe_clean_workdirs(args, config_path.parent, state_dir)
    return 0


def _maybe_clean_workdirs(args: argparse.Namespace, project_root: Path, state_dir: Path) -> None:
    if getattr(args, "clean_workdirs", False):
        removed, branches = clean_state_dir_workdirs(project_root, state_dir)
        print(
            f"Cleaned workdirs: {removed} worktree(s) removed, "
            f"{len(branches)} worker branch(es) deleted"
        )


def clean_state_dir_workdirs(project_root: Path, state_dir: Path) -> tuple[int, list[str]]:
    """Remove git worktrees under ``state_dir`` and the branches they held.

    2026-07-10 E2E: ``zf stop`` left worker worktrees/branches behind, so the
    next flow sharing the product repo died on ``worker/dev-lane-0 is already
    used by worktree ...``. Ownership is precise — only worktrees whose path
    is inside this state dir (and therefore only branches this flow checked
    out) are touched; other flows' worktrees are invisible to the filter.
    Best-effort: git errors are reported, not raised (the stop已完成).
    """
    import subprocess

    def _git(*cmd: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *cmd], cwd=project_root,
            capture_output=True, text=True, check=False,
        )

    listing = _git("worktree", "list", "--porcelain")
    if listing.returncode != 0:
        print(f"Warning: git worktree list failed: {listing.stderr.strip()}", file=sys.stderr)
        return 0, []
    state_root = state_dir.resolve()
    removed = 0
    branches: list[str] = []
    entry_path: Path | None = None
    entry_branch = ""
    entries: list[tuple[Path, str]] = []
    for line in listing.stdout.splitlines() + [""]:
        if line.startswith("worktree "):
            entry_path = Path(line[len("worktree "):].strip())
            entry_branch = ""
        elif line.startswith("branch refs/heads/"):
            entry_branch = line[len("branch refs/heads/"):].strip()
        elif not line.strip():
            if entry_path is not None:
                entries.append((entry_path, entry_branch))
            entry_path = None
            entry_branch = ""
    for path, branch in entries:
        try:
            inside = path.resolve().is_relative_to(state_root)
        except OSError:
            inside = False
        if not inside:
            continue
        result = _git("worktree", "remove", "--force", str(path))
        if result.returncode != 0:
            print(
                f"Warning: could not remove worktree {path}: {result.stderr.strip()}",
                file=sys.stderr,
            )
            continue
        removed += 1
        if branch:
            branches.append(branch)
    _git("worktree", "prune")
    deleted: list[str] = []
    for branch in branches:
        result = _git("branch", "-D", branch)
        if result.returncode == 0:
            deleted.append(branch)
        else:
            print(
                f"Warning: could not delete branch {branch}: {result.stderr.strip()}",
                file=sys.stderr,
            )
    return removed, deleted


def _should_preserve_run_manager(config, args: argparse.Namespace) -> bool:
    if config is None:
        return False
    if getattr(args, "include_run_manager", False):
        return False
    if getattr(args, "preserve_run_manager", False):
        return True
    return bool(_preserved_run_manager_roles(config))


def _preserved_run_manager_roles(config) -> set[str]:
    try:
        from zf.runtime.run_manager_resident import dedicated_resident_run_manager_role

        role = dedicated_resident_run_manager_role(config)
    except Exception:
        role = None
    if role is None:
        return set()
    return {role.instance_id}


def _write_preserve_marker_for_force(config, state_dir: Path) -> None:
    if config is None:
        return
    try:
        from zf.runtime.run_manager_resident import (
            build_resident_preserve_payload,
            write_resident_preserve_marker,
        )

        payload = build_resident_preserve_payload(
            config=config,
            state_dir=state_dir,
            reason="force_stop",
        )
        if payload is not None:
            write_resident_preserve_marker(state_dir=state_dir, payload=payload)
    except Exception:
        return
