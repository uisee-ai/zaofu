"""zf failure — materialize failure-to-eval candidates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.runtime.failure_to_eval import (
    failure_closeout_status,
    materialize_failure_candidate,
    materialize_failure_closeout,
    promote_failure_closeout_backlogs,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("failure", help="Failure-to-eval utilities")
    sub = parser.add_subparsers(dest="failure_cmd")

    materialize = sub.add_parser(
        "materialize",
        help="Materialize a failure-candidate.v1 into backlog/eval/skill draft",
    )
    materialize.add_argument("candidate", type=Path)
    materialize.add_argument("--kind", choices=["backlog", "eval", "skill", "waive"], default="backlog")
    materialize.add_argument("--output-dir", type=Path, default=Path("backlogs"))
    materialize.add_argument("--json", action="store_true")
    materialize.set_defaults(func=run_materialize)

    closeout = sub.add_parser(
        "closeout",
        help="Batch materialize failure candidates into backlog/eval/skill drafts",
    )
    closeout.add_argument("--state-dir", type=Path, default=None)
    closeout.add_argument(
        "--kinds",
        default="backlog,eval,skill",
        help="Comma-separated closeout kinds: backlog,eval,skill,waive",
    )
    closeout.add_argument("--output-root", type=Path, default=None)
    closeout.add_argument("--limit", type=int, default=None)
    closeout.add_argument("--json", action="store_true")
    closeout.set_defaults(func=run_closeout)

    promote = sub.add_parser(
        "promote",
        help="Promote closeout backlog drafts into tasks/active after owner approval",
    )
    promote.add_argument("manifest", type=Path)
    promote.add_argument("--approval-ref", required=True)
    promote.add_argument("--output-dir", type=Path, default=Path("tasks/active"))
    promote.add_argument("--limit", type=int, default=None)
    promote.add_argument("--json", action="store_true")
    promote.set_defaults(func=run_promote)

    status = sub.add_parser(
        "status",
        help="List open failure candidates lacking any four-way closeout",
    )
    status.add_argument("--state-dir", type=Path, default=None)
    status.set_defaults(func=run_status)

    parser.set_defaults(func=lambda args: _show_help(parser))


def _show_help(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 0


def run_closeout(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            load_config_with_explicit=getattr(args, "state_dir", None) is not None,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    try:
        result = materialize_failure_closeout(
            context.state_dir,
            output_root=args.output_root.expanduser() if args.output_root else None,
            kinds=[item.strip() for item in str(args.kinds or "").split(",")],
            limit=args.limit,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"failure closeout: {result.get('manifest_ref')}")
        print(
            f"materialized={result.get('materialized_count', 0)} "
            f"candidates={result.get('candidate_count', 0)}"
        )
    return 0


def run_materialize(args: argparse.Namespace) -> int:
    candidate = args.candidate.expanduser()
    if not candidate.exists():
        print(f"Error: failure candidate not found: {candidate}", file=sys.stderr)
        return 1
    try:
        output = materialize_failure_candidate(
            candidate,
            output_dir=args.output_dir.expanduser(),
            kind=args.kind,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    result = {
        "schema_version": "failure.materialize.result.v1",
        "kind": args.kind,
        "candidate_ref": str(candidate),
        "output_ref": str(output),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"failure materialized: {output}")
    return 0


def run_status(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    report = failure_closeout_status(context.state_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["open"] == 0 else 1


def run_promote(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context()
    except ConfigError as exc:
        context = None
        if str(exc).strip():
            # Promotion is a source-tree closeout action and can operate from
            # cwd before a project has a fully valid zf.yaml.
            pass
    project_root = context.project_root if context is not None else Path.cwd()
    try:
        result = promote_failure_closeout_backlogs(
            args.manifest,
            project_root=project_root,
            approval_ref=args.approval_ref,
            output_dir=args.output_dir,
            limit=args.limit,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"failure closeout promoted: {result.get('promoted_count', 0)}")
        print(f"report={result.get('report_ref')}")
    return 0
