"""zf task-doc — verify and ingest Task Capsule projections."""

from __future__ import annotations

import argparse
import os
import sys

from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.task.store import TaskStore


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("task-doc", help="Task Capsule utilities")
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml)",
    )
    parser.set_defaults(func=_run_help)
    sub = parser.add_subparsers(dest="task_doc_cmd")

    verify_p = sub.add_parser("verify", help="Verify task capsule freshness")
    verify_p.add_argument("task_id")
    verify_p.set_defaults(func=_run_verify)

    ingest_p = sub.add_parser("ingest", help="Ingest controlled task.md changes")
    ingest_p.add_argument("task_id")
    ingest_p.add_argument(
        "--operator-ack-runtime-write",
        action="store_true",
        help=(
            "Allow ingest from a worker environment. Intended only for "
            "operator/debug use; normal workers must not mutate task docs."
        ),
    )
    ingest_p.set_defaults(func=_run_ingest)


def _run_help(args: argparse.Namespace) -> int:
    if getattr(args, "task_doc_cmd", None) is not None:
        return args.func(args)
    print("Usage: zf task-doc <verify|ingest> <task_id>")
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    from zf.runtime.task_doc import verify_task_capsule

    context = resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
    )
    task = TaskStore(context.state_dir / "kanban.json").get(args.task_id)
    if task is None:
        print(f"Error: task not found: {args.task_id}", file=sys.stderr)
        return 1
    errors = verify_task_capsule(context.state_dir, task)
    if not errors:
        print(f"Task Capsule OK: {args.task_id}")
        return 0
    print(f"Task Capsule stale: {args.task_id}")
    for error in errors:
        print(f"  - {error}")
    return 1


def _run_ingest(args: argparse.Namespace) -> int:
    from zf.runtime.task_doc_ingest import ingest_task_doc

    context = resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
    )
    worker_instance = os.environ.get("ZF_ROLE_INSTANCE", "").strip()
    if worker_instance and not getattr(args, "operator_ack_runtime_write", False):
        try:
            EventWriter(
                event_log_from_project(context.state_dir, config=context.config),
            ).emit(
                "task.doc.ingest.rejected",
                actor="zf-cli",
                task_id=args.task_id,
                payload={
                    "reason": "worker_runtime_projection_write_forbidden",
                    "worker": worker_instance,
                    "command": "zf task-doc ingest",
                },
            )
        except Exception:
            pass
        print(
            "Error: worker environments may not ingest kernel-managed task docs; "
            "emit an event or run with --operator-ack-runtime-write from an "
            "operator/debug shell.",
            file=sys.stderr,
        )
        return 1
    try:
        result = ingest_task_doc(
            context.state_dir,
            args.task_id,
            event_writer=EventWriter(
                event_log_from_project(context.state_dir, config=context.config),
            ),
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    fields = ", ".join(result.updated_fields) if result.updated_fields else "(none)"
    print(f"Ingested Task Capsule: {result.task_id}")
    print(f"  updated_fields: {fields}")
    print(f"  active_dispatch_cleared: {result.active_dispatch_cleared}")
    print(f"  capsule_revision: {result.task_doc.capsule_revision}")
    return 0
