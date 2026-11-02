"""Operator CLI to re-review a proven OA role-binding defect."""

from __future__ import annotations

import argparse
import json

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.orchestrator_agent_operator_review_retry import request_review_retry


def register_recover_oa_review(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("oa-review", help="Re-review a proven OA role-binding defect")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--escalation", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(explicit_state_dir=args.state_dir, load_config_with_explicit=True)
        log = event_log_from_project(context.state_dir, config=context.config)
        from zf.cli.recover import _run_contract_recovery_allowed

        if args.apply and not _run_contract_recovery_allowed(
            context.config, log, context.state_dir, context.project_root, context.config_path,
        ):
            raise ValueError("run contract drift blocks recovery")
        result = request_review_retry(state_dir=context.state_dir, event_log=log,
            event_writer=EventWriter(log), config=context.config,
            escalation_id=args.escalation, reason=args.reason, apply=args.apply)
    except (ConfigError, ValueError, OSError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
