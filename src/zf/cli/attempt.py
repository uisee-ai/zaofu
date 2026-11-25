"""Inspect attempt artifacts and request independent Task Verify reviews."""

from __future__ import annotations

import argparse
import json
import sqlite3
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

    evaluation = sub.add_parser("skill-evaluation", help="Query Task Verify skill scorecards")
    selection = evaluation.add_mutually_exclusive_group(required=True)
    selection.add_argument("attempt_id", nargs="?")
    selection.add_argument("--run", dest="workflow_run_id")
    selection.add_argument("--task", dest="task_id")
    evaluation.add_argument("--operation", dest="operation_id")
    evaluation.add_argument("--dispatch", dest="dispatch_id")
    mode = evaluation.add_mutually_exclusive_group()
    mode.add_argument("--replay", action="store_true", help="Recompute from archived events without writing")
    mode.add_argument("--history", action="store_true", help="Include previous published revisions")
    evaluation.add_argument("--state-dir")
    evaluation.set_defaults(func=_run_skill_evaluation)

    review = sub.add_parser("skill-review", help="Independently review frozen Task Verify evidence")
    review.add_argument("attempt_id", nargs="?")
    review.add_argument("--run", dest="run_id", help="Select or disambiguate a workflow run")
    review.add_argument("--model", required=True, help="Installed local Ollama model tag")
    review.add_argument("--max-calls", type=int, default=3)
    review.add_argument("--sample-percent", type=int, default=10)
    review.add_argument("--timeout", type=int, default=90)
    review.add_argument("--state-dir")
    review.set_defaults(func=_run_skill_review)

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


def _run_skill_evaluation(args: argparse.Namespace) -> int:
    from zf.runtime.skill_evaluation_projection import FILTERS, query_cards, replay_cards

    try:
        ctx = resolve_project_context(explicit_state_dir=args.state_dir, load_config_with_explicit=True)
        filters = {key: getattr(args, key) for key in FILTERS if getattr(args, key, None)}
        if args.replay:
            cards = replay_cards(ctx.state_dir, ctx.project_root, ctx.config, **filters)
        else:
            cards = query_cards(ctx.state_dir, history=args.history, **filters)
    except (ConfigError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"cards": cards}, ensure_ascii=False, indent=2))
    return 0


def _run_skill_review(args: argparse.Namespace) -> int:
    from zf.runtime.skill_review_batch import review_batch

    try:
        ctx = resolve_project_context(explicit_state_dir=args.state_dir, load_config_with_explicit=True)
        result = review_batch(
            ctx.state_dir,
            ctx.project_root,
            ctx.config,
            run_id=args.run_id,
            attempt_id=args.attempt_id,
            model=args.model,
            max_calls=args.max_calls,
            sample_percent=args.sample_percent,
            timeout=args.timeout,
        )
    except (ConfigError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
