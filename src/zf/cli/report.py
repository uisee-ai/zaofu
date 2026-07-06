"""zf report — read-only run reports."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
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
