"""zf self-repair — run authorized harness self-repair (dispatch consumer).

backlog 0820 block B last mile. Reads autoresearch.repair.dispatch_requested
events from a run's state dir (emitted only when ZF_AUTORESEARCH_AUTO_REPAIR=
authorized + under cap), prepares an isolated ZAOFU worktree + a zf-self-repair
briefing for each, emits autoresearch.repair.dispatched, and with --spawn
launches a headless agent to run the tracked playbook. The repair targets the
harness's own code (src/zf), so it runs in the zaofu repo — not the project the
orchestrator is driving.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.events.log import EventLog
from zf.runtime.self_repair_runner import dispatch_pending_self_repairs


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "self-repair",
        help="Run authorized harness self-repair (dispatch_requested consumer)",
    )
    parser.set_defaults(func=lambda a: (parser.print_help() or 0))
    sub = parser.add_subparsers(dest="self_repair_command")

    run = sub.add_parser("run", help="Prepare/dispatch pending self-repair requests")
    run.add_argument("--state-dir", required=True, help="Run state dir with the dispatch_requested events")
    run.add_argument("--harness-root", default=None, help="ZaoFu repo root (default: detected from zf package)")
    run.add_argument("--spawn", action="store_true", help="Spawn a headless agent to run the playbook")
    run.add_argument(
        "--backend",
        default="",
        help="Headless backend id for --spawn (codex or claude-code)",
    )
    run.set_defaults(func=run_cmd)


def run_cmd(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir)
    events_path = state_dir / "events.jsonl"
    if not events_path.exists():
        print(f"no events at {events_path}", file=sys.stderr)
        return 1
    log = EventLog(events_path)
    events = log.read_all()
    log.close()
    writer = EventWriter(event_log_from_project(state_dir))
    n = dispatch_pending_self_repairs(
        events,
        writer,
        root=getattr(args, "harness_root", None),
        spawn=getattr(args, "spawn", False),
        backend=getattr(args, "backend", ""),
    )
    if n == 0:
        print("no pending self-repair dispatches")
    else:
        suffix = " + spawned" if getattr(args, "spawn", False) else ""
        print(f"{n} self-repair dispatch(es) prepared{suffix}")
    return 0
