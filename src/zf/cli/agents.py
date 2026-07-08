"""zf agents — list detected agent CLIs; unblock a parked worker lane."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


_KNOWN_AGENTS = {
    "claude": {"name": "Claude Code", "backend": "claude-code"},
    "codex": {"name": "Codex", "backend": "codex"},
}


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "agents", help="List agent CLIs / unblock a parked worker lane",
    )
    nested = parser.add_subparsers(dest="agents_command")

    unblock = nested.add_parser(
        "unblock",
        help="Clear blocked_human on a worker lane (operator redrive)",
    )
    unblock.add_argument("instance_id", help="worker instance id (e.g. dev-lane-0)")
    unblock.add_argument("--reason", required=True, help="operator reason (audit)")
    unblock.add_argument("--state-dir", type=Path, default=None)
    unblock.set_defaults(func=_run_unblock)

    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    print("Detected agent CLIs:\n")
    found = 0
    for cmd, info in _KNOWN_AGENTS.items():
        path = shutil.which(cmd)
        if path:
            print(f"  {info['name']:15s}  backend={info['backend']:15s}  path={path}")
            found += 1
        else:
            print(f"  {info['name']:15s}  backend={info['backend']:15s}  (not found)")

    print(f"\n{found}/{len(_KNOWN_AGENTS)} agent CLIs available.")
    if found == 0:
        print("To fix: install at least one agent CLI (e.g., 'npm install -g @anthropic-ai/claude-code')")
    return 0


def _run_unblock(args: argparse.Namespace) -> int:
    """A2(操作员 redrive 通道首个动作):blocked_human 的事件出口。

    发 worker.state.changed(blocked_human→idle);运行中的 orchestrator
    经 reactor `_on_worker_state_changed_event` 应用到内存,派发即恢复
    ——不再需要整 run 重启(r6.1 悬案的正解)。
    """
    from zf.core.config.loader import ConfigError
    from zf.core.config.project_context import resolve_project_context
    from zf.core.events.factory import event_log_from_project
    from zf.core.events.model import ZfEvent
    from zf.core.events.writer import EventWriter

    try:
        context = resolve_project_context(
            explicit_state_dir=args.state_dir,
            load_config_with_explicit=args.state_dir is not None,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    writer = EventWriter(event_log_from_project(
        context.state_dir, config=context.config, warn=False,
    ))
    event = writer.append(ZfEvent(
        type="worker.state.changed",
        actor="operator",
        payload={
            "instance_id": str(args.instance_id),
            "role": str(args.instance_id),
            "from": "blocked_human",
            "to": "idle",
            "reason": str(args.reason),
            "source": "zf_agents_unblock",
        },
    ))
    print(f"unblocked {args.instance_id}: {event.id}")
    return 0
