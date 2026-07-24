"""zf report — read-only run reports."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.runtime.goal_dossier import (
    GoalDossierError,
    build_cached_goal_dossier,
    write_goal_dossier_markdown,
    write_goal_dossier_projection,
)
from zf.runtime.hermes_run_report import write_hermes_run_report
from zf.runtime.run_closeout_report import write_run_closeout_report


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("report", help="Generate read-only reports")
    sub = parser.add_subparsers(dest="report_command")

    closeout = sub.add_parser(
        "run-closeout",
        help="Generate a generic run closeout markdown report",
    )
    closeout.add_argument("--state-dir", type=str, default=None)
    closeout.add_argument("--out", type=Path, required=True)
    closeout.add_argument("--title", default="Run Closeout")
    closeout.set_defaults(func=run_closeout)

    dossier = sub.add_parser(
        "goal-dossier",
        help="Generate a run-scoped Goal Dossier projection and markdown report",
    )
    dossier.add_argument("--state-dir", type=str, default=None)
    dossier.add_argument("--run-id", required=True)
    dossier.add_argument("--out", type=Path, required=True)
    dossier.set_defaults(func=run_goal_dossier)

    hermes = sub.add_parser(
        "hermes-run",
        help="Deprecated alias for run-closeout with a Hermes default title",
    )
    hermes.add_argument("--state-dir", type=str, default=None)
    hermes.add_argument("--out", type=Path, required=True)
    hermes.add_argument("--title", default="Hermes Refactor Run Closeout")
    hermes.set_defaults(func=run_hermes_run)

    parser.set_defaults(func=lambda _args: _show_help(parser))


def run_closeout(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    if state_dir is None:
        return 2
    if not (state_dir / "events.jsonl").exists():
        print(f"error: events.jsonl not found under {state_dir}", file=sys.stderr)
        return 2
    out = write_run_closeout_report(
        state_dir=state_dir,
        out=args.out,
        title=args.title,
    )
    print(str(out))
    return 0


def run_hermes_run(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    if state_dir is None:
        return 2
    if not (state_dir / "events.jsonl").exists():
        print(f"error: events.jsonl not found under {state_dir}", file=sys.stderr)
        return 2
    out = write_hermes_run_report(
        state_dir=state_dir,
        out=args.out,
        title=args.title,
    )
    print(str(out))
    return 0


def run_goal_dossier(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    if state_dir is None:
        return 2
    if not (state_dir / "events.jsonl").exists():
        print(f"error: events.jsonl not found under {state_dir}", file=sys.stderr)
        return 2
    try:
        context = resolve_project_context(
            explicit_state_dir=state_dir,
            load_config_with_explicit=True,
        )
        dossier = build_cached_goal_dossier(
            state_dir,
            args.run_id,
            project_root=context.project_root,
            config=context.config,
        )
        projection = write_goal_dossier_projection(state_dir, dossier)
        report = write_goal_dossier_markdown(args.out, dossier)
    except GoalDossierError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"projection: {projection}")
    print(f"report: {report}")
    return 0


def _state_dir(args: argparse.Namespace) -> Path | None:
    if args.state_dir:
        return Path(args.state_dir).resolve(strict=False)
    try:
        context = resolve_project_context()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None
    return context.state_dir


def _show_help(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 0
