"""Request another Judge review when historical candidate evidence becomes available."""

import argparse
from contextlib import closing
import json

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.goal_evidence_recovery import GoalEvidenceRecovery


def register_recover_goal_evidence(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "goal-evidence", help="Re-review a hold with newly bound historical evidence"
    )
    parser.add_argument("--blocked-event", required=True)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from zf.cli.recover import _run_contract_recovery_allowed

    try:
        ctx = resolve_project_context(
            explicit_state_dir=args.state_dir, load_config_with_explicit=True
        )
        with closing(event_log_from_project(ctx.state_dir, config=ctx.config)) as log:
            if args.apply and not _run_contract_recovery_allowed(
                ctx.config, log, ctx.state_dir, ctx.project_root, ctx.config_path
            ):
                raise ValueError("run contract drift blocks recovery")
            result = GoalEvidenceRecovery(
                ctx, log, EventWriter(log, default_origin="kernel")
            ).review(args.blocked_event, apply=args.apply)
    except (ConfigError, ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0
