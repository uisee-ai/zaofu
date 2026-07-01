"""`zf channel say` — agent-facing edge to post a channel message (feishu-S5).

doc 98 §3: an agent posts to a channel (and thus, via the bridge's outbound
projection of channel.message.posted, to Feishu) WITHOUT holding any Feishu
credential and WITHOUT calling an MCP/transport directly — it goes through the
existing channel-post-message ControlledAction (same whitelist/audit gate as
Web/Feishu inbound). This is the ZaoFu-native agent-facing message edge.
"""

from __future__ import annotations

import argparse
import json
import sys

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.runtime.control_actions import ControlledActionService


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("channel", help="Channel operations")
    parser.set_defaults(func=_root)
    sub = parser.add_subparsers(dest="channel_command")

    say = sub.add_parser(
        "say", help="Post a message to a channel (gated; projected to Feishu)")
    say.add_argument("channel_id")
    say.add_argument("--text", required=True)
    say.add_argument("--member-id", default="agent",
                     help="Channel member posting as (agent identity)")
    say.add_argument("--mention", action="append", default=[],
                     metavar="MEMBER", help="@mention a member (repeatable)")
    say.add_argument("--state-dir", default=None)
    say.set_defaults(func=run_say)


def _root(args: argparse.Namespace) -> int:
    print("Usage: zf channel say <channel_id> --text ...", file=sys.stderr)
    return 2


def run_say(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None))
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    actor = f"agent:{args.member_id}"
    payload = {
        "channel_id": args.channel_id,
        "member_id": args.member_id,
        "text": args.text,
        "mentions": list(args.mention),
    }
    writer = EventWriter(
        event_log_from_project(context.state_dir, config=context.config))
    requested = writer.emit("control.action.requested", actor=actor, payload=payload)
    result = ControlledActionService(
        context.state_dir, writer, config=context.config,
        actor=actor, source="cli", surface="cli",
    ).execute(action="channel-post-message", requested_action="zf channel say",
              payload=payload, requested=requested)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1
