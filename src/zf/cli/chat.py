"""zf chat — send a user message to the orchestrator agent.

Emits a user.message event to .zf/events.jsonl. The Layer 1 EventWatcher
recognizes this as a wake pattern for the Claude Code Orchestrator (Layer 2),
which then decides what to do next (decompose into features, dispatch tasks,
etc).

Without an orchestrator role in zf.yaml, this command still emits the event
but nothing reacts to it (a human or a future Layer 2 dispatcher consumes it).
"""

from __future__ import annotations

import argparse
import sys

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.factory import event_log_from_project


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("chat", help="Send a message to the orchestrator agent")
    parser.add_argument("message", nargs="+", help="The message text")
    parser.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    state_dir = context.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    log = event_log_from_project(state_dir, config=context.config)
    writer = EventWriter(log)
    message = " ".join(args.message)
    event = ZfEvent(
        type="user.message",
        actor="human",
        payload={"message": message},
    )
    event = writer.append(event)
    print(f"user.message delivered ({event.id}): {message}")
    return 0
