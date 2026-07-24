"""zf status — show current state overview.

``zf status``             → session + event count
``zf status --workers``   → per-worker state table (B3, test-plan 2026-04-15-2342)
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import sys
from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.core.state.session import SessionStore, ZfNotInitialized


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("status", help="Show current state overview")
    parser.add_argument(
        "--workers", action="store_true",
        help="Show per-worker-instance state table (derived from events.jsonl)",
    )
    parser.add_argument(
        "--dispatch", action="store_true",
        help="Show dispatch loop, worker availability, and ready-task notifications",
    )
    parser.add_argument("--json", action="store_true", help="Wrap output in zf.cli.result.v1")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    ctx = resolve_project_context()
    state_dir = ctx.state_dir

    try:
        session_store = SessionStore(state_dir / "session.yaml")
        state = session_store.load()
    except ZfNotInitialized:
        print("Error: not initialized. To fix: run 'zf init'", file=sys.stderr)
        return 1

    event_log = EventLog(state_dir / "events.jsonl")

    if getattr(args, "dispatch", False):
        return _print_dispatch_diagnostics(
            ctx,
            json_output=bool(getattr(args, "json", False)),
        )

    if getattr(args, "workers", False):
        return _print_workers(
            state_dir,
            event_log,
            ctx.config_path,
            context=ctx,
            json_output=bool(getattr(args, "json", False)),
        )

    event_count = event_log.count()
    events = event_log.query(last=1)
    last_event = events[0] if events else None

    data = {
        "session": asdict(state),
        "event_count": event_count,
        "last_event": asdict(last_event) if last_event is not None else None,
    }
    if getattr(args, "json", False):
        from zf.cli.output import print_result

        print_result(command="status", data=data, context=ctx)
        return 0

    print(f"Session:     {state.session_id}")
    print(f"Status:      {state.runtime_state}")
    print(f"Started:     {state.started_at}")
    print(f"Events:      {event_count}")
    if last_event:
        print(f"Last event:  {last_event.type} ({last_event.ts})")

    return 0


def _print_dispatch_diagnostics(ctx, *, json_output: bool = False) -> int:
    from zf.runtime.dispatch_diagnostics import build_dispatch_diagnostics

    diagnostics = build_dispatch_diagnostics(
        ctx.state_dir,
        config=ctx.config,
        project_root=ctx.project_root,
    )
    if json_output:
        from zf.cli.output import print_result

        print_result(command="status.dispatch", data=diagnostics, context=ctx)
        return 0
    loop = diagnostics.get("loop", {})
    print("Dispatch loop:")
    print(f"  status: {loop.get('status', '')}")
    print(f"  last_event: {loop.get('last_event_type', '') or '-'}")
    print(f"  age_seconds: {loop.get('age_seconds', '')}")
    print("")
    print("Worker availability:")
    for worker in diagnostics.get("worker_availability", []) or []:
        print(
            "  "
            f"{worker.get('instance_id', '')}: "
            f"{worker.get('availability', '')} "
            f"state={worker.get('state', '')} "
            f"active_task={worker.get('active_task', '') or '-'}"
        )
    print("")
    print("Dispatch notifications:")
    notifications = diagnostics.get("notifications", []) or []
    if not notifications:
        print("  (none)")
        return 0
    for item in notifications:
        print(
            "  "
            f"[{item.get('severity', 'info')}] "
            f"{item.get('kind', '')} "
            f"task={item.get('task_id', '') or '-'} "
            f"reason={item.get('reason', '')}"
        )
    return 0


def _print_workers(
    state_dir: Path,
    event_log: EventLog,
    cfg_path: Path,
    *,
    context=None,
    json_output: bool = False,
) -> int:
    """Print a table of per-instance worker state derived from recent
    ``worker.state.changed`` events.

    Runs independent of a live orchestrator — this is a pure read of
    events.jsonl + zf.yaml, so it works even if zf start isn't running.
    """
    if not cfg_path.exists():
        print("Error: zf.yaml not found in cwd", file=sys.stderr)
        return 1
    try:
        cfg = load_config(cfg_path)
    except Exception as e:
        print(f"Error loading zf.yaml: {e}", file=sys.stderr)
        return 1

    # Collect all worker instances (exclude orchestrator — it's Layer 2,
    # driven by stream-json, not a tmux worker with a lifecycle state).
    instances: list[str] = [
        r.instance_id for r in cfg.roles if r.name != "orchestrator"
    ]
    if not instances:
        print("(no worker instances configured)")
        return 0

    # Fold the recent event tail to find each instance's current state
    # + last transition time + last transition reason.
    current_state: dict[str, str] = {iid: "idle" for iid in instances}
    last_ts: dict[str, str] = {iid: "-" for iid in instances}
    last_reason: dict[str, str] = {iid: "-" for iid in instances}

    try:
        events = event_log.read_days(1)
    except Exception:
        events = []
    for event in events:
        if event.type != "worker.state.changed":
            continue
        iid = event.actor or ""
        if iid not in current_state:
            continue
        current_state[iid] = event.payload.get("to", "idle")
        last_ts[iid] = event.ts
        last_reason[iid] = event.payload.get("reason", "")

    rows = [
        {
            "instance_id": iid,
            "state": current_state[iid],
            "last_change": last_ts[iid],
            "reason": last_reason[iid],
        }
        for iid in instances
    ]
    if json_output:
        from zf.cli.output import print_result

        print_result(command="status.workers", data={"workers": rows}, context=context)
        return 0

    # Print a fixed-width table
    headers = ("WORKER", "STATE", "LAST CHANGE", "REASON")
    widths = (18, 18, 24, 60)
    print(
        f"{headers[0]:<{widths[0]}} "
        f"{headers[1]:<{widths[1]}} "
        f"{headers[2]:<{widths[2]}} "
        f"{headers[3]}"
    )
    print("-" * sum(widths))
    for iid in instances:
        state = current_state[iid]
        ts = last_ts[iid]
        # Keep timestamp short: YYYY-MM-DDTHH:MM:SS
        if len(ts) > widths[2] - 1:
            ts = ts[:widths[2] - 1]
        reason = last_reason[iid]
        if len(reason) > widths[3] - 1:
            reason = reason[: widths[3] - 4] + "..."
        print(
            f"{iid:<{widths[0]}} "
            f"{state:<{widths[1]}} "
            f"{ts:<{widths[2]}} "
            f"{reason}"
        )
    return 0
