"""Authenticated, single-worker model hand-back."""

import json
import sys

from zf.core.config.project_context import resolve_project_context
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_read_capability import authorize_artifact_read_from_environment
from zf.runtime.worker_model_handoff import REASONS, consult


def register(sub):
    parser = sub.add_parser(
        "worker-plan",
        help="Consult a frozen tool-free local model for an implementation attempt",
    )
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--reason", choices=REASONS, required=True)
    parser.add_argument(
        "--input",
        nargs=3,
        metavar=("SOURCE", "ARTIFACT", "SELECTOR"),
        action="append",
        required=True,
    )
    parser.add_argument("--state-dir")
    parser.set_defaults(func=run)


def run(args):
    try:
        ctx = resolve_project_context(
            explicit_state_dir=args.state_dir, require_config=True
        )
        state = ctx.state_dir
        manifest = load_attempt_source_manifest(state, args.attempt)
        log = event_log_from_project(state, config=ctx.config)

        def auth():
            return authorize_artifact_read_from_environment(
                state, manifest=manifest, event_log=log, use_artifact_context=True
            )

        result = consult(
            state,
            ctx.config,
            log,
            EventWriter(log),
            manifest,
            auth(),
            args.reason,
            [tuple(v) for v in args.input],
            authorize=auth,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
