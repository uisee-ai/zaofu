"""zf goal — run goal 查看与状态设置(133-G0,对应 codex /goal)。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.schema import WorkflowRunLimitsConfig
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
    set_cmd.add_argument("--timeout-seconds", type=float, default=None)
    set_cmd.add_argument("--token-budget", type=int, default=None)
    set_cmd.add_argument("--cost-budget-usd", type=float, default=None)
    set_cmd.add_argument(
        "--operation", default="", help="Budget-blocked Task Pipeline operation"
    )
    set_cmd.add_argument("--operation-max-usage-samples", type=int, default=None)
    set_cmd.add_argument(
        "--all-run-operations", action="store_true",
        help="Apply the sample limit to all operations in this Run",
    )
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
    try:
        run_limits_patch = _run_limits_patch(args)
    except (TypeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    operation_id = str(getattr(args, "operation", "") or "").strip()
    sample_limit = getattr(args, "operation_max_usage_samples", None)
    all_operations = bool(getattr(args, "all_run_operations", False))
    if all_operations and not operation_id:
        print("Error: --all-run-operations requires --operation", file=sys.stderr)
        return 2
    if bool(operation_id) != (sample_limit is not None):
        print(
            "Error: --operation and --operation-max-usage-samples must be provided together",
            file=sys.stderr,
        )
        return 2
    if sample_limit is not None and (
        sample_limit < 0 or status != "active" or run_limits_patch
        or not str(getattr(args, "reason", "") or "").strip()
    ):
        print(
            "Error: operation amendment requires --status active, a nonnegative sample limit, an approval reason, and no run limit patch",
            file=sys.stderr,
        )
        return 2
    if not objective and not status and not run_limits_patch:
        print(
            "Error: provide --objective, --status, and/or a run limit",
            file=sys.stderr,
        )
        return 2
    try:
        context = _context(args)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    writer = EventWriter(event_log_from_project(
        context.state_dir, config=context.config, warn=False,
    ))
    projection = _projection(context.state_dir, context.config)
    run_id = str(projection.get("run_id") or "").strip()
    payload: dict = {"source": "zf_goal_cli"}
    if run_id:
        payload.update({"run_id": run_id, "workflow_run_id": run_id})
    if objective:
        payload["objective"] = objective
    if status:
        payload["status"] = status
    if run_limits_patch:
        payload["run_limits_patch"] = run_limits_patch
    if args.reason:
        payload["reason"] = str(args.reason)
    if operation_id:
        payload["operation_budget_id"] = operation_id
        payload["operation_limits_patch"] = {"max_usage_samples": sample_limit}
        if all_operations:
            payload["operation_budget_scope"] = "run"
        from zf.runtime.workflow_operation import reduce_workflow_operations
        from zf.runtime.workflow_operation_task_pipeline import (
            find_task_pipeline_budget_amendment,
        )

        events = writer.event_log.read_all()
        operation = reduce_workflow_operations(events).get(operation_id, {})
        candidate = ZfEvent(
            type="run.goal.updated", actor="operator", payload=payload,
            correlation_id=run_id or None,
        )
        if find_task_pipeline_budget_amendment([*events, candidate], operation) is not candidate:
            print(
                "Error: operation is not eligible or sample limit does not cover the blocked budget",
                file=sys.stderr,
            )
            return 1
    event = writer.append(ZfEvent(
        type="run.goal.updated",
        actor="operator",
        payload=payload,
        correlation_id=run_id or None,
    ))
    if status == "active" and run_id:
        from zf.core.task.store import TaskStore
        from zf.runtime.run_admission import build_run_admission_projection
        from zf.runtime.workflow_task_lifecycle import (
            reopen_workflow_managed_task_from_run_recovery,
        )

        entry = build_run_admission_projection(writer.event_log.read_all()).runs.get(run_id)
        if entry is not None and entry.active:
            reopen_workflow_managed_task_from_run_recovery(
                task_store=TaskStore(context.state_dir / "kanban.json"),
                event_writer=writer,
                recovery_event=event,
            )
    # codex re-activate 语义:set 回 active = 唤醒(quiescent 的
    # _WAKE_EVENT_TYPES 含 run.goal.updated,tick 服务自动恢复点火)。
    print(f"run.goal.updated appended: {event.id}")
    return 0


def _run_limits_patch(args: argparse.Namespace) -> dict[str, float | int]:
    values = {
        "timeout_seconds": getattr(args, "timeout_seconds", None),
        "token_budget": getattr(args, "token_budget", None),
        "cost_budget_usd": getattr(args, "cost_budget_usd", None),
    }
    patch = {key: value for key, value in values.items() if value is not None}
    if not patch:
        return {}
    try:
        WorkflowRunLimitsConfig(
            timeout_seconds=float(patch.get("timeout_seconds", 0.0)),
            token_budget=int(patch.get("token_budget", 0)),
            cost_budget_usd=float(patch.get("cost_budget_usd", 0.0)),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    return patch


__all__ = ["register"]
