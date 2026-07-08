"""zf goal — run goal 查看与状态设置(133-G0,对应 codex /goal)。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter

_SETTABLE_STATUSES = (
    "active",
    "paused",
    "blocked",
    "complete",
    "usage_limited",
    "budget_limited",
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("goal", help="Show or set the run goal")
    nested = parser.add_subparsers(dest="goal_command")

    show = nested.add_parser("show", help="Show run goal projection")
    show.add_argument("--state-dir", type=Path, default=None)
    show.add_argument("--json", action="store_true", dest="as_json")
    show.set_defaults(func=_run_show)

    set_cmd = nested.add_parser("set", help="Set run goal objective/status")
    set_cmd.add_argument("--state-dir", type=Path, default=None)
    set_cmd.add_argument("--objective", default="")
    set_cmd.add_argument("--status", default="", choices=("", *_SETTABLE_STATUSES))
    set_cmd.add_argument("--reason", default="", help="operator reason (audit)")
    set_cmd.set_defaults(func=_run_set)

    parser.set_defaults(func=_run_show_default)


def _context(args: argparse.Namespace):
    return resolve_project_context(
        explicit_state_dir=args.state_dir,
        load_config_with_explicit=args.state_dir is not None,
    )


def _projection(state_dir: Path, config) -> dict:
    from zf.runtime.run_manager import build_run_goal_projection

    events = event_log_from_project(state_dir, config=config, warn=False).read_all()
    return build_run_goal_projection(events)


def _run_show_default(args: argparse.Namespace) -> int:
    if not hasattr(args, "as_json"):
        args.as_json = False
    return _run_show(args)


def _run_show(args: argparse.Namespace) -> int:
    try:
        context = _context(args)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    projection = _projection(context.state_dir, context.config)
    if getattr(args, "as_json", False):
        print(json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"status: {projection.get('status')}")
        objective = str(projection.get("objective") or "")
        if objective:
            print(f"objective: {objective}")
        print(f"run_id: {projection.get('run_id') or '-'}")
    return 0


def _run_set(args: argparse.Namespace) -> int:
    objective = str(args.objective or "").strip()
    status = str(args.status or "").strip()
    if not objective and not status:
        print("Error: provide --objective and/or --status", file=sys.stderr)
        return 2
    try:
        context = _context(args)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    writer = EventWriter(event_log_from_project(
        context.state_dir, config=context.config, warn=False,
    ))
    payload: dict = {"source": "zf_goal_cli"}
    if objective:
        payload["objective"] = objective
    if status:
        payload["status"] = status
    if args.reason:
        payload["reason"] = str(args.reason)
    event = writer.append(ZfEvent(
        type="run.goal.updated",
        actor="operator",
        payload=payload,
    ))
    # codex re-activate 语义:set 回 active = 唤醒(quiescent 的
    # _WAKE_EVENT_TYPES 含 run.goal.updated,tick 服务自动恢复点火)。
    print(f"run.goal.updated appended: {event.id}")
    return 0


__all__ = ["register"]
