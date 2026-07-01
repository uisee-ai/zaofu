"""zf cleanup — periodic maintenance tasks."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from zf.core.config.project_context import resolve_project_context
from zf.core.safety import PathGuard, PathGuardError
from zf.core.security.nonce import NonceManager


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("cleanup", help="Run periodic cleanup")
    parser.add_argument("--periodic", action="store_true", help="Run full periodic cleanup cycle")
    parser.add_argument(
        "--checkpoints", type=int, default=5,
        help="(deprecated, kept for backward-compatible CLI args — checkpoints "
             "were removed 2026-04-20; this flag is now a no-op)",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Runtime state dir (default: project.state_dir from zf.yaml)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    context = resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
        load_config_with_explicit=True,
    )
    state_dir = context.state_dir
    if not state_dir.exists():
        print(
            f"Error: state dir {state_dir} not found. To fix: run 'zf init'",
            file=sys.stderr,
        )
        return 1

    actions: list[str] = []

    # 1. Sweep any legacy .zf/checkpoints/ dir (removed 2026-04-20; keep
    #    this cleanup path indefinitely to remove accumulated snapshots
    #    from older installs).
    checkpoints_dir = state_dir / "checkpoints"
    if checkpoints_dir.exists():
        try:
            _assert_cleanup_delete(checkpoints_dir, state_dir)
        except PathGuardError as e:
            print(f"Error: unsafe cleanup path: {e}", file=sys.stderr)
            return 1
        shutil.rmtree(checkpoints_dir)
        actions.append("Removed legacy .zf/checkpoints/ directory")

    # 2. Clean expired nonces
    nonce_dir = state_dir / "nonces"
    if nonce_dir.exists():
        nonce_mgr = NonceManager(nonce_dir)
        expired = nonce_mgr.cleanup()
        if expired:
            actions.append(f"Cleaned {expired} expired nonces")

    # 3. Remove stale lock if the harness session is gone
    lock_path = state_dir / "loop.lock"
    if lock_path.exists():
        from zf.runtime.transport import make_transport, TmuxTransport
        from zf.runtime.tmux import TmuxSession
        try:
            config = context.config
            if config is None:
                raise RuntimeError("missing zf.yaml")
            transport = make_transport(config)
        except Exception:
            transport = TmuxTransport(TmuxSession(session_name="zf"))
        if not transport.is_session_running():
            try:
                PathGuard.assert_under(lock_path, state_dir)
                PathGuard.assert_not_truth_file(lock_path)
            except PathGuardError as e:
                print(f"Error: unsafe cleanup path: {e}", file=sys.stderr)
                return 1
            lock_path.unlink()
            actions.append("Removed stale loop.lock")

    if actions:
        print("Cleanup completed:")
        for a in actions:
            print(f"  - {a}")
    else:
        print("Nothing to clean up.")

    return 0


def _assert_cleanup_delete(path: Path, state_dir: Path) -> None:
    PathGuard.assert_under(path, state_dir)
    PathGuard.assert_safe_to_delete(path)
