"""zf stop — stop the harness loop."""

from __future__ import annotations

import argparse
import subprocess
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
    cleanup = parser.add_mutually_exclusive_group()
    cleanup.add_argument(
        "--resume-cleanup", action="store_true",
        help="Resume only previously requested workdir cleanup; do not include new workdirs",
    )
    cleanup.add_argument(
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
        from zf.core.events.factory import event_log_from_project
        from zf.core.events.writer import EventWriter
        EventWriter(event_log_from_project(state_dir, config=config)).emit(
            "loop.shutdown_requested", actor="zf-cli", payload={"mode": "force", "source": "operator"})
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
        try:
            from zf.runtime.feishu_projection_sidecar import (
                stop_feishu_projection_sidecar_by_pidfile,
            )

            stop_feishu_projection_sidecar_by_pidfile(state_dir)
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
    if getattr(args, "clean_workdirs", False) or getattr(args, "resume_cleanup", False):
        if getattr(args, "resume_cleanup", False):
            from zf.runtime.workspace_cleanup_recovery import resume_cleanup
            try:
                removed, branches = resume_cleanup(project_root, state_dir)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                print(f"Warning: cleanup could not resume: {exc}", file=sys.stderr)
                return
        else:
            removed, branches = clean_state_dir_workdirs(project_root, state_dir)
        print(
            f"Cleaned workdirs: {removed} worktree(s) removed, "
            f"{len(branches)} worker branch(es) deleted"
        )


def clean_state_dir_workdirs(project_root: Path, state_dir: Path) -> tuple[int, list[str]]:
    """Persist an exact cleanup request and execute its recoverable steps."""
    from zf.runtime.workspace_cleanup_recovery import request_cleanup
    try:
        return request_cleanup(project_root, state_dir)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Warning: cleanup could not start: {exc}", file=sys.stderr)
        return 0, []


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
