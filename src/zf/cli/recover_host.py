"""Host supervision checks lifecycle eligibility without changing business state."""

import json

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.runtime.host_lifecycle import host_restart_eligibility


def register_recover_host(subparsers):
    parser = subparsers.add_parser("host-check", help="Read-only host restart eligibility (exit 1 means do not restart)")
    parser.add_argument("--state-dir", default=None)
    parser.set_defaults(func=run)


def run(args):
    try:
        ctx = resolve_project_context(explicit_state_dir=args.state_dir, load_config_with_explicit=True)
        log = event_log_from_project(ctx.state_dir, config=ctx.config)
        result = host_restart_eligibility(ctx.state_dir, log.read_all())
    except (ConfigError, OSError, ValueError) as exc:
        result = {"eligible": False, "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["eligible"] else 1
