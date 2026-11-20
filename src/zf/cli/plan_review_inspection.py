"""Read-only operator diagnostics, without impersonating a worker reader."""
import json
import sys

from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.runtime.plan_review_inspection import inspect_review


def register(sub):
    parser = sub.add_parser("inspect-review", help="Inspect Critic input cost, scope and read progress")
    parser.add_argument("--attempt", required=True)
    parser.set_defaults(func=run)


def run(args):
    try:
        context = resolve_project_context(explicit_state_dir=args.state_dir)
        events = event_log_from_project(context.state_dir, config=context.config).read_all()
        result = inspect_review(context.state_dir, args.attempt, events, config=context.config)
        output = json.dumps(result, ensure_ascii=False)
        if len(output.encode()) > 16384:
            raise ValueError("review diagnostic exceeds metadata output limit")
    except (OSError, ValueError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0
