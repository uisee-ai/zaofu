"""zf guard — read-only runtime guards for workers."""

from __future__ import annotations

import argparse
import json
import sys

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.task.store import TaskStore


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("guard", help="Read-only worker guard checks")
    sub = parser.add_subparsers(dest="guard_cmd")

    ownership = sub.add_parser(
        "ownership",
        help="Verify the actor still owns the active task before emitting completion",
    )
    ownership.add_argument("--task", required=True, help="Task id to check")
    ownership.add_argument("--actor", required=True, help="Worker actor/instance id")
    ownership.add_argument("--state-dir", default=None, help="Runtime state dir")
    ownership.add_argument("--json", action="store_true", help="Print JSON result")
    ownership.set_defaults(func=_run_ownership)

    parser.set_defaults(func=_run_help)


def _run_help(args: argparse.Namespace) -> int:
    print("Usage: zf guard ownership --task TASK --actor ACTOR", file=sys.stderr)
    return 2


def _run_ownership(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(explicit_state_dir=args.state_dir)
    except ConfigError as exc:
        return _emit_result(
            args,
            ok=False,
            code="config_error",
            message=str(exc),
            rc=2,
        )
    task = TaskStore(context.state_dir / "kanban.json").get(args.task)
    if task is None:
        return _emit_result(
            args,
            ok=False,
            code="unknown_task",
            message=f"task {args.task} not found",
            rc=2,
        )
    expected = task.assigned_to or ""
    actor = str(args.actor or "")
    if not expected:
        return _emit_result(
            args,
            ok=False,
            code="unassigned_task",
            message=f"task {args.task} is not assigned",
            rc=3,
            expected=expected,
            actual=actor,
        )
    if not _assignee_equivalent(actor, expected, context.config):
        return _emit_result(
            args,
            ok=False,
            code="actor_not_assigned",
            message=(
                f"task {args.task} assigned_to={expected!r}, "
                f"actor={actor!r}"
            ),
            rc=3,
            expected=expected,
            actual=actor,
        )
    return _emit_result(
        args,
        ok=True,
        code="ok",
        message=f"task {args.task} is owned by {actor}",
        rc=0,
        expected=expected,
        actual=actor,
    )


def _assignee_equivalent(actor: str, expected: str, config: object | None) -> bool:
    if actor == expected:
        return True
    roles = getattr(config, "roles", []) or []
    for role in roles:
        name = getattr(role, "name", "")
        instance_id = getattr(role, "instance_id", "")
        if (actor == name and expected == instance_id) or (
            actor == instance_id and expected == name
        ):
            return True
    return False


def _emit_result(
    args: argparse.Namespace,
    *,
    ok: bool,
    code: str,
    message: str,
    rc: int,
    expected: str = "",
    actual: str = "",
) -> int:
    payload = {
        "ok": ok,
        "code": code,
        "task_id": getattr(args, "task", ""),
        "expected": expected,
        "actual": actual,
        "message": message,
    }
    if getattr(args, "json", False):
        stream = sys.stdout if ok else sys.stderr
        print(json.dumps(payload, ensure_ascii=False), file=stream)
    else:
        stream = sys.stdout if ok else sys.stderr
        print(message, file=stream)
    return rc
