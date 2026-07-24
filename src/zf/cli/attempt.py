"""Read-only attempt artifact inspection."""

from __future__ import annotations

import argparse
import json
import sys

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.runtime.artifact_query import ArtifactQueryService


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("attempt", help="Attempt-level artifact queries")
    sub = parser.add_subparsers(dest="attempt_cmd")

    inspect = sub.add_parser("inspect", help="Inspect attempt inputs and handoff state")
    inspect.add_argument("attempt_id")
    inspect.add_argument("--state-dir", default=None)
    inspect.set_defaults(func=_run_inspect)

    missing = sub.add_parser("missing-reads", help="List missing required reads")
    missing.add_argument("attempt_id")
    missing.add_argument("--state-dir", default=None)
    missing.set_defaults(func=_run_missing_reads)

    parser.set_defaults(func=lambda _args: parser.print_help() or 0)


def _service(args: argparse.Namespace) -> ArtifactQueryService:
    context = resolve_project_context(
        explicit_state_dir=args.state_dir,
        load_config_with_explicit=True,
    )
    return ArtifactQueryService(
        state_dir=context.state_dir,
        project_root=context.project_root,
        config=context.config,
    )


def _run_inspect(args: argparse.Namespace) -> int:
    try:
        service = _service(args)
        result = service.attempt_inspect(
            args.attempt_id,
            context=service.context(
                actor="operator",
                purpose="attempt-inspect",
                mode="canonical",
            ),
        )
    except (ConfigError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _run_missing_reads(args: argparse.Namespace) -> int:
    try:
        service = _service(args)
        result = service.attempt_missing_reads(
            args.attempt_id,
            context=service.context(
                actor="operator",
                purpose="attempt-inspect",
                mode="canonical",
            ),
        )
    except (ConfigError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
