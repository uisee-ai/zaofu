"""Confirm receipt of the current operation's portable handoff."""
import json
import sys

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.runtime.result_submit import credential_from_environment
from zf.runtime.task_handoff_receipt import accept_task_handoff


def register(subparsers):
    parser = subparsers.add_parser("handoff-accept", help="Acknowledge a completely read current handoff, without completing the task")
    parser.add_argument("--operation", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--state-dir", default=None)
    parser.set_defaults(func=run)


def run(args):
    try:
        context = resolve_project_context(explicit_state_dir=args.state_dir)
        log = event_log_from_project(context.state_dir, config=context.config)
        role, credential = credential_from_environment()
        result = accept_task_handoff(state_dir=context.state_dir, event_log=log,
            operation_id=args.operation, role=role, credential=credential, digest=args.digest)
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
