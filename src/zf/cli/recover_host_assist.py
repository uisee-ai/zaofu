"""Produce a host-assistance preview; applying it requires the operator Web gate."""
import json

from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.task_host_manifest import preview


def register(subparsers):
    parser = subparsers.add_parser("host-assist", help="Preview exact task host assistance without executing it")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--kind", choices=["commit", "verify"], required=True)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--command-id", default="")
    parser.set_defaults(func=run)


def run(args):
    try:
        context = resolve_project_context(explicit_state_dir=args.state_dir, load_config_with_explicit=True)
        service = ControlledActionService(context.state_dir,
            EventWriter(event_log_from_project(context.state_dir, config=context.config)),
            config=context.config, project_root=context.project_root, actor="operator", surface="cli")
        result = preview(service, operation_id=args.operation, kind=args.kind,
                         paths=args.path, command_id=args.command_id)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
