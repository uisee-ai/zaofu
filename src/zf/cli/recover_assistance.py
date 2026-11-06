"""Request or read guidance for the current running worker attempt."""

import argparse
import json

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.worker_assistance import read_assistance, request_assistance
from zf.runtime.workflow_operation import load_workflow_operation


def register_recover_assistance(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("assistance", help="Attempt-bound read-only guidance")
    parser.add_argument("mode", choices=["request", "read"])
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--operation-id", default="")
    parser.add_argument("--attempt-id", default="")
    parser.add_argument("--lease-id", default="")
    parser.add_argument("--actor", default="")
    parser.add_argument("--owner", choices=["orchestrator", "run_manager"], default="orchestrator")
    parser.add_argument("--question", default="")
    parser.add_argument("--evidence-ref", action="append", default=[])
    parser.add_argument("--tried", action="append", default=[])
    parser.add_argument("--request-id", default="")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        ctx = resolve_project_context(explicit_state_dir=args.state_dir, load_config_with_explicit=True)
        log = event_log_from_project(ctx.state_dir, config=ctx.config)
        writer = EventWriter(log)
        if args.mode == "read":
            result = read_assistance(state_dir=ctx.state_dir, writer=writer, request_id=args.request_id)
        else:
            operation = load_workflow_operation(log, args.operation_id) or {}
            result = request_assistance(state_dir=ctx.state_dir, writer=writer, config=ctx.config,
                identity={"operation_id": args.operation_id, "active_attempt_id": args.attempt_id,
                          "lease_id": args.lease_id, "workflow_run_id": operation.get("workflow_run_id"),
                          "task_id": operation.get("task_id")}, actor=args.actor, owner=args.owner,
                question=args.question, evidence_refs=args.evidence_ref, tried=args.tried)
    except (ConfigError, ValueError, OSError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
