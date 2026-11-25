"""Inspect configured jobs and run one previously authorized inspection now."""

import argparse
from hmac import compare_digest
import json
import hashlib
from types import SimpleNamespace
import sys
import time
from zf.runtime.call_result_envelope import canonical_json_sha256

from zf.core.config.project_context import resolve_project_context
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.runtime.workflow_schedule import ACTOR, PREFIX, definitions, reconcile_schedules


def register(sub):
    parser = sub.add_parser(
        "schedule", help="Inspect persistent job history or run a configured inspection"
    )
    parser.add_argument("id")
    parser.add_argument("--state-dir")
    parser.add_argument(
        "--run-now", metavar="REQUEST_ID", help="Stable ID for this manual trigger"
    )
    parser.add_argument(
        "--authorization-token",
        default="",
        help="Operator authorization for a manual trigger",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        ctx = resolve_project_context(
            explicit_state_dir=args.state_dir, load_config_with_explicit=True
        )
        log = event_log_from_project(ctx.state_dir, config=ctx.config)
        job = next((d for d in definitions(ctx.config) if d["id"] == args.id), None)
        if job is None:
            raise ValueError("schedule definition does not exist")
        if args.run_now is not None:
            expected = job.get("manual_token_sha256", "")
            supplied = hashlib.sha256(args.authorization_token.encode()).hexdigest()
            if (
                not args.authorization_token
                or not expected
                or not compare_digest(expected, supplied)
            ):
                raise ValueError("operator authorization required")
            if not args.run_now.strip() or len(args.run_now) > 256:
                raise ValueError("a bounded manual request ID is required")
            if job["action"] not in {"inspect", "agent_report"} or not job.get("enabled"):
                raise ValueError(
                    "manual run requires an enabled independent inspection"
                )
            runtime = SimpleNamespace(
                config=ctx.config,
                state_dir=ctx.state_dir,
                event_log=log,
                event_writer=EventWriter(log),
            )
            reconcile_schedules(runtime, manual_id=args.run_now, definition_id=args.id)
            if job["action"] == "agent_report":
                occurrence = canonical_json_sha256([job["id"], job["revision"], "manual:" + args.run_now])
                until = time.monotonic() + job["timeout_seconds"] + 10
                while True:
                    rows = [e for e in log.read_all() if e.origin == "kernel" and e.actor == ACTOR
                            and e.payload.get("occurrence") == occurrence]
                    if not rows:
                        raise ValueError("manual model job was not claimed; inspect the active job and occurrence budget")
                    if any(e.type == PREFIX + "finished" for e in rows):
                        break
                    if time.monotonic() >= until:
                        raise ValueError("model report outcome is pending reconciliation")
                    time.sleep(.1)
                    reconcile_schedules(runtime, manual_id=args.run_now, definition_id=args.id)
        history = [
            e.payload
            for e in log.read_all()
            if e.origin == "kernel"
            and e.actor == ACTOR
            and e.type.startswith(PREFIX)
            and e.payload.get("definition_id") == args.id
        ]
        print(
            json.dumps(
                {
                    "definition_id": args.id,
                    "revision": job["revision"],
                    "enabled": job.get("enabled", False),
                    "history": history,
                },
                ensure_ascii=False,
            )
        )
    except (ValueError, OSError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
