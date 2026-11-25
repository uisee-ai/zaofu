"""Read the current consumer's frozen milestone and independently admitted status."""

import argparse
import json
import sys

from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_read_capability import authorize_artifact_read_from_environment
from zf.runtime.milestone_handoff import milestone_readiness
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


def run_milestone(args: argparse.Namespace) -> int:
    try:
        ctx = resolve_project_context(explicit_state_dir=args.state_dir)
        log = event_log_from_project(ctx.state_dir, config=ctx.config)
        manifest = load_attempt_source_manifest(ctx.state_dir, args.attempt)
        identity = authorize_artifact_read_from_environment(
            ctx.state_dir, manifest=manifest, event_log=log
        )
        op = load_workflow_operation(log, identity["operation_id"])
        if not op or op.get("active_attempt_id") != args.attempt:
            raise ValueError("milestone operation is no longer current")
        request = hydrate_sidecar_ref(ctx.state_dir, op["request_ref"]).payload[
            "request"
        ]
        handoff = hydrate_sidecar_ref(
            ctx.state_dir, request["milestone_handoff_ref"]
        ).payload
        if handoff.get("consumer") != {
            "actor": identity["actor"],
            "operation_id": identity["operation_id"],
            "attempt_id": args.attempt,
        }:
            raise ValueError("milestone consumer differs")
        value = milestone_readiness(ctx.state_dir, log.read_all(), handoff)
    except (OSError, KeyError, ValueError) as exc:
        print(f"Error: milestone unavailable: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(value, ensure_ascii=False))
    return 0


def register_operator(sub):
    parser = sub.add_parser(
        "milestone",
        help="Inspect readiness of a frozen milestone after its worker has exited",
    )
    parser.add_argument("operation")
    parser.add_argument("--state-dir")
    parser.set_defaults(func=operator_view)


def operator_view(args):
    try:
        ctx = resolve_project_context(explicit_state_dir=args.state_dir)
        log = event_log_from_project(ctx.state_dir, config=ctx.config)
        op = load_workflow_operation(log, args.operation)
        if not op:
            raise ValueError("unknown operation")
        request = hydrate_sidecar_ref(ctx.state_dir, op["request_ref"]).payload[
            "request"
        ]
        handoff = hydrate_sidecar_ref(
            ctx.state_dir, request["milestone_handoff_ref"]
        ).payload
        if handoff["consumer"]["operation_id"] != args.operation:
            raise ValueError("milestone operation differs")
        print(
            json.dumps(
                milestone_readiness(ctx.state_dir, log.read_all(), handoff),
                ensure_ascii=False,
            )
        )
        return 0
    except (KeyError, ValueError, OSError) as exc:
        print(f"Error: milestone unavailable: {exc}", file=sys.stderr)
        return 1
