"""Credential-gated public messages scoped to a Run and generation."""

import json
from types import SimpleNamespace
import sys

from zf.core.config.project_context import resolve_project_context
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_read_capability import authorize_artifact_read_from_environment
from zf.runtime.workflow_board import current_board_identity, inbox, post


def register(sub):
    p = sub.add_parser(
        "board", help="Read and acknowledge public messages in the bound Run"
    )
    p.add_argument("--attempt", required=True)
    p.add_argument("--state-dir")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--roster", action="store_true", help="List authorized members and mention groups")
    mode.add_argument("--post", metavar="STABLE_MESSAGE_ID")
    mode.add_argument("--ack", metavar="MESSAGE_KEY")
    mode.add_argument("--seen", metavar="MESSAGE_KEY")
    p.add_argument("--recipient", action="append", default=[])
    p.add_argument("--text", default="")
    p.set_defaults(func=run)


def run(args):
    try:
        ctx = resolve_project_context(
            explicit_state_dir=args.state_dir, load_config_with_explicit=True
        )
        log = event_log_from_project(ctx.state_dir, config=ctx.config)
        manifest = load_attempt_source_manifest(ctx.state_dir, args.attempt)
        identity = authorize_artifact_read_from_environment(
            ctx.state_dir, manifest=manifest, event_log=log
        )
        runtime = SimpleNamespace(
            config=ctx.config,
            state_dir=ctx.state_dir,
            event_log=log,
            event_writer=EventWriter(log),
        )
        if args.post:
            result = post(runtime, identity, args.post, args.recipient, args.text)
        else:
            if args.text or args.recipient:
                raise ValueError("recipients and text require a post")
            if args.roster:
                _, board = current_board_identity(runtime, identity)
                result = {key: board[key] for key in ("generation", "members", "groups") if key in board}
            else:
                result = inbox(
                    runtime, identity, acknowledge=args.ack or "", seen=args.seen or ""
                )
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
