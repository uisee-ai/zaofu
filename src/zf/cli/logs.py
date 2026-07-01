"""zf logs — view harness and role logs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.config.project_context import resolve_project_context


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("logs", help="View harness logs")
    parser.add_argument("role", nargs="?", default=None, help="Role name (omit for all)")
    parser.add_argument("--tail", type=int, default=50, help="Number of lines to show")
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Runtime state dir (default: project.state_dir from zf.yaml)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    state_dir = resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
    ).state_dir
    logs_dir = state_dir / "logs"

    if not logs_dir.exists():
        print("No logs directory found. To fix: run 'zf init' first.", file=sys.stderr)
        return 1

    role = getattr(args, "role", None)
    tail = getattr(args, "tail", 50)

    if role:
        log_file = logs_dir / f"{role}.log"
        if not log_file.exists():
            available = [f.stem for f in logs_dir.glob("*.log")]
            print(f"No log for role '{role}'. Available: {available}", file=sys.stderr)
            return 1
        _print_tail(log_file, tail, role)
    else:
        log_files = sorted(logs_dir.glob("*.log"))
        if not log_files:
            print("No log files found.")
            return 0
        for log_file in log_files:
            _print_tail(log_file, tail, log_file.stem)

    return 0


def _print_tail(path: Path, n: int, label: str) -> None:
    """Print the last N lines of a log file."""
    lines = path.read_text().splitlines()
    tail_lines = lines[-n:] if len(lines) > n else lines
    print(f"--- {label} (last {len(tail_lines)} lines) ---")
    for line in tail_lines:
        print(f"  {line}")
    print()
