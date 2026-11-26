"""Signal-aware command loop for the Autoresearch resident."""

from __future__ import annotations

import fcntl
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from zf.autoresearch.resident import actions_json, run_resident_once


def run_resident_cli(args: Any, *, state_dir: Path) -> int:
    if not args.execute:
        return _run_resident_loop(args, state_dir=state_dir)
    # The launcher lock dies with the watcher; this lock belongs to the actual
    # consumer, including orphaned residents and direct one-shot CLI calls.
    lock_path = state_dir.resolve() / "processes" / "autoresearch-resident-execution.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"Autoresearch resident already running for {state_dir}", file=sys.stderr)
            return 1
        # Never unlink: waiters must always contend on the same inode. Closing
        # the non-inheritable descriptor releases ownership even after a crash.
        return _run_resident_loop(args, state_dir=state_dir)


def _run_resident_loop(args: Any, *, state_dir: Path) -> int:
    output_root = (
        args.output_root
        if args.output_root is not None
        else state_dir / "autoresearch" / "resident"
    )
    interval = max(float(getattr(args, "interval_seconds", 10.0) or 10.0), 0.1)
    max_actions_per_tick = max(
        int(getattr(args, "max_actions_per_tick", 0) or 0),
        0,
    )
    shutdown_requested = threading.Event()

    def _request_shutdown(_signum, _frame) -> None:
        shutdown_requested.set()

    previous_sigterm = signal.signal(signal.SIGTERM, _request_shutdown)
    try:
        while not shutdown_requested.is_set():
            if args.execute:
                from zf.autoresearch.interrupted_checkpoint import reconcile_interrupted
                from zf.core.events import EventWriter
                from zf.core.events.factory import event_log_from_project
                reconcile_interrupted(EventWriter(event_log_from_project(state_dir)), state_dir=state_dir)
            actions = run_resident_once(
                state_dir=state_dir,
                worktree_root=args.worktree_root,
                output_root=output_root,
                execute=args.execute,
                self_repair_consumer=args.self_repair_consumer,
                self_repair_spawn=args.self_repair_spawn,
                self_repair_backend=args.self_repair_backend,
                max_actions_per_tick=max_actions_per_tick,
                should_stop=shutdown_requested.is_set,
            )
            print(actions_json(actions), flush=True)
            from zf.runtime.autoresearch_parent_exit import parent_completed
            from zf.core.events.factory import event_log_from_project
            # A watcher may admit another Run after one goal completes. Only a
            # handed-off (or standalone) resident drains its finite queue and exits.
            launcher = os.environ.get("ZF_AUTORESEARCH_LAUNCHER_PID")
            if not actions and launcher != str(os.getppid()):
                log = event_log_from_project(state_dir)
                try:
                    if parent_completed(log.read_all()):
                        break
                finally:
                    log.close()
            if shutdown_requested.is_set() or not getattr(args, "watch", False):
                break
            shutdown_requested.wait(interval)
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


__all__ = ["run_resident_cli"]
