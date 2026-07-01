"""zf autopilot — deterministic proposal-only runner."""

from __future__ import annotations

import argparse
import json
import sys

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.runtime.autopilot import run_autopilot_tick


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "autopilot",
        help="Run deterministic Autopilot proposal checks",
    )
    sub = parser.add_subparsers(dest="autopilot_cmd")

    tick = sub.add_parser("tick", help="Scan runtime state and create proposals")
    tick.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml)",
    )
    tick.add_argument(
        "--dry-run",
        action="store_true",
        help="Show proposals without writing events.jsonl",
    )
    tick.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON",
    )
    tick.set_defaults(func=run_tick)
    parser.set_defaults(func=run_help)


def run_help(args: argparse.Namespace) -> int:
    print("Usage: zf autopilot tick [--dry-run] [--json] [--state-dir PATH]")
    return 0


def run_tick(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            require_config=True,
            load_config_with_explicit=True,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not context.state_dir.exists():
        print("Error: runtime state dir 不存在,请先运行 zf init。", file=sys.stderr)
        return 1

    result = run_autopilot_tick(
        context.state_dir,
        config=context.config,
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps({
            "enabled": result.enabled,
            "mode": result.mode,
            "dry_run": result.dry_run,
            "created_count": result.created_count,
            "skipped_duplicates": result.skipped_duplicates,
            "proposals": [proposal.payload() for proposal in result.created],
        }, ensure_ascii=False, indent=2))
        return 0

    if not result.enabled:
        print("Autopilot 未启用: zf.yaml autopilot.enabled=false")
        return 0

    action = "候选" if result.dry_run else "创建"
    print(
        f"Autopilot tick 完成: {action} {result.created_count} 个 proposal, "
        f"跳过 {result.skipped_duplicates} 个重复项。"
    )
    for proposal in result.created:
        payload = proposal.payload()
        print(
            f"- {payload['proposal_id']} {payload['kind']} "
            f"{payload.get('task_id') or 'project'}: {payload['reason']}"
        )
    return 0
