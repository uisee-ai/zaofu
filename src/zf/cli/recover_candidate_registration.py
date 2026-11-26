"""Preview or explicitly authorize one same-Run Candidate registration retry."""

import json

from zf.cli.recover import _run_contract_recovery_allowed
from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.candidate_registration_resume import registration_preview
from zf.runtime.control_actions import ControlledActionService


def register(subparsers):
    parser = subparsers.add_parser(
        "candidate-registration",
        help="Retry an untouched frozen Reader roster on its original Run",
    )
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--failure", required=True, help="Kernel fanout.cancelled event id")
    parser.add_argument(
        "--approval-ref",
        default="",
        help="Operator authorization for one exact registration retry",
    )
    parser.add_argument("--apply", action="store_true")
    parser.set_defaults(func=run)


def run(args):
    try:
        context = resolve_project_context(explicit_state_dir=args.state_dir, load_config_with_explicit=True)
        log = event_log_from_project(context.state_dir, config=context.config)
        writer = EventWriter(log, default_origin="kernel")
        service = ControlledActionService(
            context.state_dir,
            writer,
            config=context.config,
            project_root=context.project_root,
            actor="operator",
            source="cli",
            surface="cli",
        )
        payload = registration_preview(service, args.failure)
        if args.apply:
            if not args.approval_ref.strip():
                raise ValueError("an operator approval reference is required")
            if not _run_contract_recovery_allowed(
                context.config,
                log,
                context.state_dir,
                context.project_root,
                context.config_path,
                fail_closed=True,
            ):
                raise ValueError("run contract drift blocks recovery")
            payload["approval_ref"] = args.approval_ref.strip()
            requested = writer.emit(
                "control.action.requested",
                actor="operator",
                causation_id=args.failure,
                correlation_id=payload["workflow_run_id"],
                payload=payload,
            )
            result = service.execute(
                action=payload["action"],
                requested_action="zf recover candidate-registration",
                payload=payload,
                requested=requested,
            )
        else:
            result = {"ok": True, "status": "preview", **payload}
    except (ConfigError, ValueError, KeyError, OSError) as exc:
        result = {"ok": False, "status": "rejected", "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1
