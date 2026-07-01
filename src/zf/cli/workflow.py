"""zf workflow — workflow topology + per-task audit utilities."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.workflow.inspection import (
    build_workflow_inspection_report,
    inspection_failed,
)
from zf.core.workflow.inspection_render import (
    render_workflow_inspection_markdown,
    write_workflow_inspection_artifacts,
)
from zf.core.workflow.topology import WorkflowEventSets, WorkflowTopology
from zf.runtime.gate_projection import project_gate_projection
from zf.runtime.hook_registry import project_hook_registry
from zf.runtime.profile_policy import gate_policy_for_task
from zf.runtime.stage_contract import evaluate_stage_contract


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("workflow", help="Inspect workflow topology")
    sub = parser.add_subparsers(dest="workflow_cmd")

    render = sub.add_parser("render", help="Render linear and star topology")
    render.set_defaults(func=_run_render)

    inspect = sub.add_parser(
        "inspect",
        help="Preflight inspect workflow graph, handoff, affinity, and skills",
    )
    inspect.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to zf.yaml (default: nearest project zf.yaml)",
    )
    inspect.add_argument("--format", choices=["md", "json"], default="md")
    inspect.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on WARN as well as STOP",
    )
    inspect.add_argument(
        "--write-artifact",
        action="store_true",
        help="Write inspect JSON/MD under project.state_dir artifacts",
    )
    inspect.set_defaults(func=_run_inspect)

    # EVAL-WORKFLOW-AUDIT-001 (doc 43 §2.3): per-task completeness audit
    audit = sub.add_parser(
        "audit",
        help="Audit task workflow completeness (required_events, stage_order)",
    )
    audit.add_argument(
        "--task", default=None,
        help="Single task id (otherwise audits all in_progress tasks)",
    )
    audit.add_argument(
        "--since", default=None,
        help="Time window (e.g. 24h, 7d) — only audit tasks active in window",
    )
    audit.add_argument(
        "--format", choices=["md", "json"], default="md",
    )
    audit.add_argument(
        "--strict", action="store_true",
        help="Exit 1 if any task is partial / non-compliant",
    )
    audit.add_argument(
        "--state-dir", default=None,
    )
    audit.set_defaults(func=_run_audit)

    gates = sub.add_parser(
        "gates",
        help="Render the effective read-only gate projection",
    )
    gates.add_argument("--format", choices=["md", "json"], default="md")
    gates.add_argument("--state-dir", default=None)
    gates.set_defaults(func=_run_gates)

    hooks = sub.add_parser(
        "hooks",
        help="Render the effective read-only hook registry",
    )
    hooks.add_argument("--format", choices=["md", "json"], default="md")
    hooks.add_argument("--state-dir", default=None)
    hooks.set_defaults(func=_run_hooks)

    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    print(
        "Usage: zf workflow render | zf workflow inspect | "
        "zf workflow audit | zf workflow gates | zf workflow hooks",
        file=sys.stderr,
    )
    return 2


def _run_render(args: argparse.Namespace) -> int:
    try:
        config = load_config(Path.cwd() / "zf.yaml")
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    topology = WorkflowTopology.from_config(config)
    print(topology.full_ascii_render())
    return 0


def _run_inspect(args: argparse.Namespace) -> int:
    try:
        if args.config is not None:
            config_path = args.config.expanduser().resolve()
            config = load_config(config_path)
            project_root = config_path.parent
            state_dir = Path(config.project.state_dir)
            if not state_dir.is_absolute():
                state_dir = project_root / state_dir
        else:
            context = resolve_project_context(require_config=True)
            if context.config is None:
                raise ConfigError(f"Config file not found: {context.config_path}")
            config = context.config
            project_root = context.project_root
            state_dir = context.state_dir
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    report = build_workflow_inspection_report(
        config,
        project_root=project_root,
        state_dir=state_dir,
    )
    artifact_refs = {}
    if args.write_artifact:
        artifact_refs = write_workflow_inspection_artifacts(
            report,
            state_dir=state_dir,
        )
        report["artifact_refs"] = artifact_refs
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_workflow_inspection_markdown(report), end="")
        if artifact_refs:
            print("Artifacts:")
            for kind, path in artifact_refs.items():
                print(f"  - {kind}: {path}")
    return 1 if inspection_failed(report, strict=args.strict) else 0


# ---------------------------------------------------------------------------
# EVAL-WORKFLOW-AUDIT-001
# ---------------------------------------------------------------------------


# Stage order — derived from WorkflowEventSets baseline. Used to detect
# stage_order violations (e.g. judge.passed before static_gate.passed).
_DEFAULT_STAGE_ORDER: tuple[str, ...] = (
    "task.dispatched",
    "arch.proposal.done",
    "design.critique.done",
    "dev.build.done",
    "static_gate.passed",
    "review.approved",
    "test.passed",
    "judge.passed",
)


def _parse_since(since: str | None) -> datetime | None:
    """Parse '24h' / '7d' / '30m' into a cutoff datetime in UTC."""
    if not since:
        return None
    s = since.strip().lower()
    if s.endswith("h"):
        hours = float(s[:-1])
        return datetime.now(timezone.utc) - timedelta(hours=hours)
    if s.endswith("d"):
        days = float(s[:-1])
        return datetime.now(timezone.utc) - timedelta(days=days)
    if s.endswith("m"):
        minutes = float(s[:-1])
        return datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return None


def audit_task(
    task_id: str,
    events: Iterable,
    event_sets: WorkflowEventSets,
    *,
    task: Task | None = None,
    config=None,
    state_dir: Path | None = None,
    project_root: Path | None = None,
) -> dict:
    """Audit one task. Returns a dict suitable for both md + json output."""
    events_list = list(events)
    task_events = [e for e in events_list if getattr(e, "task_id", "") == task_id]
    if not task_events:
        return {
            "task_id": task_id,
            "status": "no_events",
            "evidence_completeness": 0.0,
            "covered_events": [],
            "missing_events": [],
            "stage_order_violations": [],
        }

    # Determine which stages this task reached.
    seen_types = {}
    for e in task_events:
        seen_types.setdefault(e.type, e)

    # Required events for a "complete" task — handoff success set.
    required = list(event_sets.handoff_success_events) + ["task.dispatched"]
    covered = []
    missing = []
    for req in required:
        if req in seen_types:
            covered.append({
                "type": req,
                "event_id": getattr(seen_types[req], "id", ""),
                "ts": getattr(seen_types[req], "ts", ""),
            })
        else:
            missing.append(req)

    # Stage order check — find any event later in _DEFAULT_STAGE_ORDER
    # that has timestamp earlier than an event before it.
    violations = []
    stage_events = [
        (st, seen_types[st]) for st in _DEFAULT_STAGE_ORDER
        if st in seen_types
    ]
    for i in range(1, len(stage_events)):
        prev_st, prev_ev = stage_events[i - 1]
        curr_st, curr_ev = stage_events[i]
        prev_ts = getattr(prev_ev, "ts", "")
        curr_ts = getattr(curr_ev, "ts", "")
        if prev_ts and curr_ts and curr_ts < prev_ts:
            violations.append(
                f"{curr_st} ({curr_ts}) emitted before {prev_st} ({prev_ts})"
            )

    # Completeness — covered / required.
    completeness = len(covered) / len(required) if required else 1.0
    status = "complete" if not missing and not violations else "partial"

    report = {
        "task_id": task_id,
        "status": status,
        "evidence_completeness": completeness,
        "covered_events": covered,
        "missing_events": missing,
        "stage_order_violations": violations,
    }
    if task is not None and config is not None:
        report["gate_policy"] = gate_policy_for_task(
            task,
            config=config,
        ).to_dict()
    if task is not None and config is not None and state_dir is not None:
        stage_contracts = [
            evaluate_stage_contract(
                stage=stage,
                task=task,
                events=events_list,
                state_dir=state_dir,
                project_root=project_root,
            ).to_dict()
            for stage in config.workflow.stages
            if (
                stage.criteria.success_criteria
                or stage.criteria.output.required_keys
                or stage.criteria.output.required_artifacts
                or stage.criteria.output.artifact_kinds
            )
        ]
        if stage_contracts:
            report["stage_contracts"] = stage_contracts
    return report


def _run_audit(args: argparse.Namespace) -> int:
    """EVAL-WORKFLOW-AUDIT-001: per-task completeness audit."""
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    state_dir = context.state_dir
    if not state_dir.exists():
        print(f"Error: state dir {state_dir} does not exist", file=sys.stderr)
        return 1

    config = context.config
    event_log = event_log_from_project(state_dir, config=config)
    task_store = TaskStore(state_dir / "kanban.json")
    event_sets = WorkflowEventSets.baseline()

    cutoff = _parse_since(getattr(args, "since", None))
    all_events = event_log.read_all()
    if cutoff:
        cutoff_iso = cutoff.isoformat()
        all_events = [e for e in all_events if (e.ts or "") >= cutoff_iso]

    # Determine which tasks to audit.
    if args.task:
        task_ids = [args.task]
    else:
        tasks = task_store.list_all_with_archive()
        if cutoff:
            # Only tasks with at least one event in window
            tasks_with_events = {
                e.task_id for e in all_events if getattr(e, "task_id", "")
            }
            task_ids = [t.id for t in tasks if t.id in tasks_with_events]
        else:
            task_ids = [
                t.id for t in tasks
                if t.status in ("in_progress", "review", "test", "judge", "done")
            ]
    task_ids = sorted(set(task_ids))

    task_by_id = {task.id: task for task in task_store.list_all_with_archive()}
    reports = [
        audit_task(
            tid,
            all_events,
            event_sets,
            task=task_by_id.get(tid),
            config=config,
            state_dir=state_dir,
            project_root=context.project_root,
        )
        for tid in task_ids
    ]
    has_partial = any(r["status"] != "complete" for r in reports)

    if args.format == "json":
        out = {
            "audited": len(reports),
            "complete": sum(1 for r in reports if r["status"] == "complete"),
            "partial": sum(1 for r in reports if r["status"] == "partial"),
            "no_events": sum(1 for r in reports if r["status"] == "no_events"),
            "tasks": reports,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        window = args.since or "all"
        print(f"Workflow Audit · {window} window · {len(reports)} task(s)\n")
        for r in reports:
            status_icon = {
                "complete": "✓",
                "partial": "⚠",
                "no_events": "—",
            }.get(r["status"], "?")
            print(f"{r['task_id']}: {status_icon} {r['status']}")
            if r["status"] == "no_events":
                continue
            print(f"  evidence_completeness: "
                  f"{len(r['covered_events'])}/"
                  f"{len(r['covered_events']) + len(r['missing_events'])} "
                  f"({r['evidence_completeness']*100:.0f}%)")
            for ev in r["covered_events"]:
                print(f"    ✓ {ev['type']} ({ev['event_id']})")
            for miss in r["missing_events"]:
                print(f"    ✗ {miss} — MISSING")
            for viol in r["stage_order_violations"]:
                print(f"    ⚠ stage_order: {viol}")
            print()

    if args.strict and has_partial:
        return 1
    return 0


def _run_gates(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            load_config_with_explicit=True,
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    event_log = event_log_from_project(context.state_dir, config=context.config)
    projection = project_gate_projection(
        context.state_dir,
        config=context.config,
        project_root=context.project_root,
        events=event_log.read_all(),
    )
    if args.format == "json":
        print(json.dumps(projection, indent=2, ensure_ascii=False))
    else:
        _print_gate_projection_md(projection)
    return 0


def _run_hooks(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            load_config_with_explicit=True,
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    event_log = event_log_from_project(context.state_dir, config=context.config)
    registry = project_hook_registry(
        context.state_dir,
        config=context.config,
        project_root=context.project_root,
        events=event_log.read_all(),
    )
    if args.format == "json":
        print(json.dumps(registry, indent=2, ensure_ascii=False))
    else:
        _print_hook_registry_md(registry)
    return 0


def _print_gate_projection_md(projection: dict) -> None:
    summary = projection.get("summary", {}) or {}
    print("Gate Projection\n")
    print(f"- schema: {projection.get('schema_version', '')}")
    print(f"- gates: {summary.get('gates', 0)}")
    print(f"- blocking: {summary.get('blocking', 0)}")
    print(f"- warnings: {summary.get('warnings', 0)}")
    print()
    for gate in projection.get("gates", []) or []:
        print(
            f"- {gate.get('id', '')}: {gate.get('status', '')} "
            f"[{gate.get('surface', '')}]"
        )
        reason = gate.get("reason")
        if reason:
            print(f"  reason: {reason}")


def _print_hook_registry_md(registry: dict) -> None:
    summary = registry.get("summary", {}) or {}
    print("Hook Registry\n")
    print(f"- schema: {registry.get('schema_version', '')}")
    print(f"- hooks: {summary.get('hooks', 0)}")
    print(f"- configured: {summary.get('configured', 0)}")
    print(f"- wired: {summary.get('wired', 0)}")
    print(f"- experimental_unwired: {summary.get('experimental_unwired', 0)}")
    print()
    for hook in registry.get("hooks", []) or []:
        print(
            f"- {hook.get('id', '')}: {hook.get('status', '')} "
            f"{hook.get('event_type', '')}"
        )
        reason = hook.get("reason")
        if reason:
            print(f"  reason: {reason}")
