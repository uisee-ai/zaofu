"""zf runs / zf archive-run — run archive operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.run_archive import (
    RunArchiveError,
    RunProjector,
    archive_run,
    close_test_task_for_passed_run,
    normalize_run_status,
    read_task_runs,
    reconcile_runs,
    run_and_archive_command,
    validate_run_id,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    archive_p = subparsers.add_parser(
        "archive-run",
        help="Archive a run live state into .zf/runs/<run_id>",
    )
    _add_common_project_args(archive_p)
    archive_p.add_argument("--run-id", required=True)
    archive_p.add_argument("--trace-id", default="")
    archive_p.add_argument("--test-task-id", default="")
    archive_p.add_argument("--scenario-id", default="")
    archive_p.add_argument("--target-project-id", default="")
    archive_p.add_argument("--target-config", default="")
    archive_p.add_argument("--preset", default="")
    archive_p.add_argument("--status", default="passed")
    archive_p.add_argument("--exit-code", type=int, default=None)
    archive_p.add_argument("--live-state-dir", type=Path, required=True)
    archive_p.add_argument("--run-root", type=Path, default=None)
    archive_p.add_argument("--timeout", type=float, default=None)
    archive_p.add_argument(
        "--command",
        default="",
        help="Optional command to execute before archiving. If omitted, archives only.",
    )
    archive_p.set_defaults(func=run_archive_command)

    runs_p = subparsers.add_parser("runs", help="Inspect and reconcile run archives")
    _add_common_project_args(runs_p)
    runs_p.add_argument("--json", action="store_true", help="Wrap output in zf.cli.result.v1")
    runs_sub = runs_p.add_subparsers(dest="runs_command")

    list_p = runs_sub.add_parser("list", help="List projected runs")
    list_p.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Wrap output in zf.cli.result.v1",
    )
    list_p.set_defaults(func=run_list)

    rebuild_p = runs_sub.add_parser("rebuild", help="Rebuild run projections")
    rebuild_p.set_defaults(func=run_rebuild)

    reconcile_p = runs_sub.add_parser("reconcile", help="Reconcile stale active runs")
    reconcile_p.add_argument("--stale-after", type=float, default=900.0)
    reconcile_p.set_defaults(func=run_reconcile)

    task_p = runs_sub.add_parser("for-task", help="List runs for a task id")
    task_p.add_argument("task_id")
    task_p.set_defaults(func=run_for_task)

    explain_p = runs_sub.add_parser(
        "explain",
        help="Explain run/stage/attempt state from shadow spine projections (131-P0)",
    )
    explain_p.add_argument("--task", default="", help="Only show this task's attempts")
    explain_p.add_argument(
        "--no-refresh", action="store_true",
        help="Skip incremental fold of new events before reading projections",
    )
    explain_p.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Wrap output in zf.cli.result.v1",
    )
    explain_p.set_defaults(func=run_explain)
    runs_p.set_defaults(func=run_list)


def _add_common_project_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Project root. Defaults to current ProjectContext root.",
    )


def _context(args: argparse.Namespace):
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return None
    project_root = Path(getattr(args, "project_root", None) or context.project_root)
    return context, project_root.resolve(strict=False)


def run_archive_command(args: argparse.Namespace) -> int:
    resolved = _context(args)
    if resolved is None:
        return 2
    context, project_root = resolved
    state_dir = context.state_dir
    live_state_dir = Path(args.live_state_dir).resolve(strict=False)
    trace_id = args.trace_id or f"trace-{args.run_id}"
    try:
        validate_run_id(args.run_id)
        if args.command:
            result = run_and_archive_command(
                project_root=project_root,
                state_dir=state_dir,
                live_state_dir=live_state_dir,
                run_id=args.run_id,
                trace_id=trace_id,
                test_task_id=args.test_task_id,
                scenario_id=args.scenario_id,
                target_project_id=args.target_project_id,
                target_config=args.target_config,
                preset=args.preset,
                command=args.command,
                run_root=args.run_root,
                timeout=args.timeout,
            )
        else:
            event_log = EventLog(state_dir / "events.jsonl")
            writer = EventWriter(event_log, correlation_id=trace_id)
            status = normalize_run_status(args.status)
            writer.emit(
                "run.started",
                actor="zf-cli",
                task_id=args.test_task_id or None,
                correlation_id=trace_id,
                payload={
                    "run_id": args.run_id,
                    "scenario_id": args.scenario_id,
                    "target_project_id": args.target_project_id,
                    "target_config": args.target_config,
                    "live_state_dir": str(live_state_dir),
                    "status": "running",
                },
            )
            completion_event = writer.emit(
                "run.completed",
                actor="zf-cli",
                task_id=args.test_task_id or None,
                correlation_id=trace_id,
                payload={
                    "run_id": args.run_id,
                    "status": status,
                    "exit_code": args.exit_code,
                    "validation_status": status,
                },
            )
            close_test_task_for_passed_run(
                state_dir=state_dir,
                event_log=event_log,
                writer=writer,
                test_task_id=args.test_task_id,
                run_id=args.run_id,
                status=status,
                completion_event=completion_event,
                trace_id=trace_id,
            )
            result = archive_run(
                project_root=project_root,
                state_dir=state_dir,
                live_state_dir=live_state_dir,
                run_id=args.run_id,
                trace_id=trace_id,
                test_task_id=args.test_task_id,
                scenario_id=args.scenario_id,
                target_project_id=args.target_project_id,
                target_config=args.target_config,
                preset=args.preset,
                command=args.command,
                status=status,
                exit_code=args.exit_code,
                run_root=args.run_root,
            )
            writer.emit(
                "run.archived",
                actor="zf-cli",
                task_id=args.test_task_id or None,
                correlation_id=trace_id,
                payload={
                    "run_id": args.run_id,
                    "artifact_dir": str(result.artifact_dir),
                    "artifact_manifest": str(result.manifest_path),
                },
            )
            RunProjector(
                project_root=project_root,
                state_dir=state_dir,
                event_log=event_log,
            ).rebuild(write=True)
    except (RunArchiveError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps({
        "run_id": result.run_id,
        "status": result.status,
        "artifact_dir": str(result.artifact_dir),
        "manifest": str(result.manifest_path),
    }, ensure_ascii=False, indent=2))
    return 0


def run_rebuild(args: argparse.Namespace) -> int:
    resolved = _context(args)
    if resolved is None:
        return 2
    context, project_root = resolved
    result = RunProjector(project_root=project_root, state_dir=context.state_dir).rebuild(write=True)
    print(json.dumps({
        "active_runs": len(result.active.get("active_runs") or []),
        "runs": len(result.index.get("runs") or []),
    }, indent=2))
    return 0


def run_reconcile(args: argparse.Namespace) -> int:
    resolved = _context(args)
    if resolved is None:
        return 2
    context, project_root = resolved
    result = reconcile_runs(
        project_root=project_root,
        state_dir=context.state_dir,
        stale_after_seconds=float(args.stale_after),
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0 if not result.errors else 1


def run_list(args: argparse.Namespace) -> int:
    resolved = _context(args)
    if resolved is None:
        return 2
    context, project_root = resolved
    projector = RunProjector(project_root=project_root, state_dir=context.state_dir)
    from zf.runtime.workflow_spine_projection import (
        read_spine_explain,
        refresh_spine_projections,
    )

    refresh_spine_projections(
        context.state_dir,
        EventLog(context.state_dir / "events.jsonl"),
    )
    spine = read_spine_explain(context.state_dir)
    workflow_runs = [
        {"run_id": run_id, **(entry if isinstance(entry, dict) else {})}
        for run_id, entry in sorted((spine.get("runs") or {}).items())
    ]
    data = {
        "active": projector.load_active().get("active_runs") or [],
        "runs": projector.load_index().get("runs") or [],
        "workflow_runs": workflow_runs,
    }
    if getattr(args, "json", False):
        from zf.cli.output import print_result

        print_result(command="runs.list", data=data, context=context)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def run_explain(args: argparse.Namespace) -> int:
    resolved = _context(args)
    if resolved is None:
        return 2
    context, _project_root = resolved
    state_dir = context.state_dir
    from zf.runtime.workflow_spine_projection import (
        read_spine_explain,
        refresh_spine_projections,
    )
    if not args.no_refresh:
        refresh_spine_projections(state_dir, EventLog(state_dir / "events.jsonl"))
    out = read_spine_explain(state_dir, task_id=str(args.task or ""))
    if getattr(args, "json", False):
        from zf.cli.output import print_result

        print_result(command="runs.explain", data=out, context=context)
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def run_for_task(args: argparse.Namespace) -> int:
    resolved = _context(args)
    if resolved is None:
        return 2
    context, project_root = resolved
    rows = read_task_runs(
        project_root=project_root,
        state_dir=context.state_dir,
        task_id=args.task_id,
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0
