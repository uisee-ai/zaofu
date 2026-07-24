"""zf task trace — reconstruct a task lifecycle from events + kanban.

Subcommands:
  trace <task_id> [--format=table|json] [--causation]
      Print the full lifecycle of a task: every event, in order,
      annotated with actor / time / causation link.

Intended consumers:
  - human operators doing post-mortem on a slow / stuck task
  - LH-6 autoresearch loop (MetricsCollector + trace → results.tsv row)
  - LH-5.T4 `zf check trace-integrity`
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.events.model import ZfEvent
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("task", help="Task-level tools")
    sub = parser.add_subparsers(dest="task_cmd")

    trace = sub.add_parser("trace", help="Reconstruct task lifecycle")
    trace.add_argument("task_id", help="Task ID (e.g. TASK-ABCDEF)")
    trace.add_argument("--format", choices=["table", "json"],
                        default="table")
    trace.add_argument("--causation", action="store_true",
                        help="Show causation parent for each event")
    trace.add_argument(
        "--state-dir",
        default=None,
        help="Runtime state dir (default: project.state_dir from zf.yaml)",
    )
    trace.set_defaults(func=_run_trace)

    create = sub.add_parser(
        "create-from-contract",
        help="Atomically create feature/task/contract and optionally assign it",
    )
    create.add_argument("--title", required=True, help="Task title")
    create.add_argument(
        "--contract-file",
        required=True,
        help="JSON contract file; accepts either the contract object or {'contract': {...}}",
    )
    create.add_argument("--feature-id", default="", help="Existing feature id")
    create.add_argument(
        "--feature-title",
        default="",
        help="Create a feature with this title when --feature-id is omitted",
    )
    create.add_argument("--feature-description", default="")
    create.add_argument("--message", default="", help="Original user message")
    create.add_argument("--priority", type=int, default=3)
    create.add_argument("--key", default="", help="Task idempotency key")
    create.add_argument("--assign", default="", help="Assign task to this role")
    create.add_argument("--id-only", action="store_true")
    create.add_argument("--json", action="store_true")
    create.add_argument(
        "--state-dir",
        default=None,
        help="Runtime state dir (default: project.state_dir from zf.yaml)",
    )
    create.set_defaults(func=_run_create_from_contract)

    artifacts = sub.add_parser(
        "artifacts",
        help="List artifact occurrences linked to one task",
    )
    artifacts.add_argument("task_id")
    artifacts.add_argument("--limit", type=int, default=200)
    artifacts.add_argument("--state-dir", default=None)
    artifacts.set_defaults(func=_run_artifacts)

    parser.set_defaults(func=lambda a: _show_help(parser))


def _show_help(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 0


def _state_dir(args: argparse.Namespace | None = None) -> Path:
    return resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
    ).state_dir


def _task_store(args: argparse.Namespace) -> TaskStore:
    return TaskStore(_state_dir(args) / "kanban.json")


def _feature_store(args: argparse.Namespace) -> FeatureStore:
    return FeatureStore(_state_dir(args) / "feature_list.json")


def _event_writer(args: argparse.Namespace) -> EventWriter:
    return EventWriter(EventLog(_state_dir(args) / "events.jsonl"))


def _collect(
    task_id: str,
    args: argparse.Namespace | None = None,
) -> tuple[list[ZfEvent], object | None]:
    sd = _state_dir(args)
    events = EventLog(sd / "events.jsonl").read_all()
    tasks = TaskStore(sd / "kanban.json")
    task = tasks.get(task_id)
    if task is None:
        # Check archive.
        for t in tasks.list_all_with_archive():
            if t.id == task_id:
                task = t
                break
    filtered = [e for e in events if e.task_id == task_id]
    return filtered, task


def _fmt_ts(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts


def _run_trace(args: argparse.Namespace) -> int:
    events, task = _collect(args.task_id, args)
    if not events and task is None:
        print(f"Error: no events or kanban entry for {args.task_id}",
              file=sys.stderr)
        return 1

    if args.format == "json":
        payload = {
            "task_id": args.task_id,
            "task": asdict(task) if task else None,
            "events": [asdict(e) for e in events],
            "event_count": len(events),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    # Table / tree output
    title = task.title if task else "(task not in kanban)"
    status = task.status if task else "?"
    print(f"Task {args.task_id} — {title!r} [{status}]")
    if not events:
        print("  (no events for this task)")
        return 0

    first_ts = events[0].ts
    last_ts = events[-1].ts
    id_to_idx = {e.id: i for i, e in enumerate(events)}
    for i, e in enumerate(events):
        marker = "└─" if i == len(events) - 1 else "├─"
        info = (
            f"{marker} {e.type}"
            f"  actor={e.actor or '?'}"
            f"  at {_fmt_ts(e.ts)}"
        )
        if args.causation and e.causation_id:
            parent_type = None
            if e.causation_id in id_to_idx:
                parent_type = events[id_to_idx[e.causation_id]].type
            info += f"  ← {e.causation_id[:12]}"
            if parent_type:
                info += f" ({parent_type})"
        print(f"  {info}")

    # Footer: totals
    try:
        dt0 = datetime.fromisoformat(first_ts)
        dt1 = datetime.fromisoformat(last_ts)
        dur_min = (dt1 - dt0).total_seconds() / 60.0
    except Exception:
        dur_min = 0.0
    rework = sum(1 for e in events
                 if e.type in ("review.rejected", "test.failed",
                               "judge.failed", "gate.failed",
                               "discriminator.failed"))
    print(f"\nTotal: {len(events)} events · "
          f"duration {dur_min:.1f}min · rework {rework}")
    return 0


def _run_artifacts(args: argparse.Namespace) -> int:
    context = resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
        load_config_with_explicit=True,
    )
    from zf.runtime.artifact_query import ArtifactQueryService

    service = ArtifactQueryService(
        state_dir=context.state_dir,
        project_root=context.project_root,
        config=context.config,
    )
    result = service.task_artifacts(
        args.task_id,
        context=service.context(
            actor="operator",
            purpose="task-artifacts",
            mode="canonical",
            limit=args.limit,
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _run_create_from_contract(args: argparse.Namespace) -> int:
    try:
        contract = _load_contract(Path(args.contract_file))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    task_store = _task_store(args)
    feature_store = _feature_store(args)
    writer = _event_writer(args)

    feature_id = str(args.feature_id or contract.feature_id or "").strip()
    feature: Feature | None = None
    if feature_id:
        feature = feature_store.get(feature_id)
        if feature is None:
            print(f"Error: feature {feature_id} not found", file=sys.stderr)
            return 1
    else:
        feature = Feature(
            title=args.feature_title or args.title,
            description=args.feature_description,
            priority=args.priority,
            user_message=args.message or args.feature_title or args.title,
        )
        feature_store.add(feature)
        feature_id = feature.id
        writer.append(ZfEvent(
            type="feature.created",
            actor="zf-cli",
            payload={
                "feature_id": feature.id,
                "title": feature.title,
                "priority": feature.priority,
            },
        ))

    contract.feature_id = feature_id
    task = Task(
        title=args.title,
        key=args.key,
        assigned_to=args.assign or "",
        contract=contract,
    )
    task = task_store.add(task)
    writer.append(ZfEvent(
        type="task.created",
        actor="zf-cli",
        task_id=task.id,
        payload={
            "feature_id": feature_id,
            "key": task.key,
            "source": "task.create-from-contract",
        },
    ))
    writer.append(ZfEvent(
        type="task.contract.update",
        actor="zf-cli",
        task_id=task.id,
        payload={
            "contract": asdict(contract),
            "source": "task.create-from-contract",
        },
    ))
    if args.assign:
        writer.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id=task.id,
            payload={"role": args.assign, "assignee": args.assign},
        ))

    result = {
        "feature_id": feature_id,
        "task_id": task.id,
        "title": task.title,
        "assigned_to": task.assigned_to,
    }
    if args.id_only:
        print(task.id)
    elif args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        assigned = f" assigned_to={task.assigned_to}" if task.assigned_to else ""
        print(f"Created {task.id}: {task.title} feature_id={feature_id}{assigned}")
    return 0


def _load_contract(path: Path) -> TaskContract:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"cannot read contract file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("contract file must contain a JSON object")
    data = raw.get("contract") if isinstance(raw.get("contract"), dict) else raw
    allowed = {field.name for field in fields(TaskContract)}
    filtered = {
        key: value
        for key, value in data.items()
        if key in allowed
    }
    acceptance = filtered.get("acceptance")
    if isinstance(acceptance, list):
        filtered["acceptance"] = "\n".join(str(item) for item in acceptance)
    return TaskContract(**filtered)
