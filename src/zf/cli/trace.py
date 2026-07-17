"""zf trace — inspect event traces."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.trace import TraceQuery


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("trace", help="Inspect event traces")
    sub = parser.add_subparsers(dest="trace_cmd")

    show = sub.add_parser("show", help="Show a trace by correlation, event, or task id")
    show.add_argument("trace_id", help="Correlation id, event id, or task id")
    show.add_argument("--format", choices=["table", "json"], default="table")
    show.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    show.set_defaults(func=run_show)

    record = sub.add_parser("record-fixture", help="Record a fanout replay fixture")
    record.add_argument("fanout_id")
    record.add_argument("--output", required=True)
    record.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    record.set_defaults(func=run_record_fixture)

    replay = sub.add_parser("replay-fixture", help="Replay a fanout fixture")
    replay.add_argument("fixture_path")
    replay.set_defaults(func=run_replay_fixture)

    # ZF-OBS-SPAN-001 integration (2026-05-18): zf trace spans projects
    # events.jsonl into span records under .zf/traces/spans.jsonl plus
    # optional per-run rollup.
    spans = sub.add_parser(
        "spans",
        help="Project events.jsonl into span records (OBS-SPAN-001)",
    )
    spans.add_argument(
        "--state-dir", type=str, default=None,
        help="Path to runtime state dir (default: project.state_dir)",
    )
    spans.add_argument(
        "--run-id", type=str, default=None,
        help="Optional run id; if given, also writes per-run rollup",
    )
    spans.set_defaults(func=run_spans)

    operation = sub.add_parser(
        "operation",
        help="Show dispatch-scoped operation timeline",
    )
    operation.add_argument("dispatch_id")
    operation.add_argument("--format", choices=["table", "json"], default="json")
    operation.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir)",
    )
    operation.set_defaults(func=run_operation)

    workflow_operation = sub.add_parser(
        "workflow-operation",
        help="Show stable workflow-operation and call-result timeline",
    )
    workflow_operation.add_argument("operation_id")
    workflow_operation.add_argument("--format", choices=["table", "json"], default="json")
    workflow_operation.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir)",
    )
    workflow_operation.set_defaults(func=run_workflow_operation)

    # #V (TR-TRACE-GANTT-001, cangjie 2026-05-22 r4 operator UX):
    # per-dev swim-lane Gantt + dep DAG as Mermaid markdown / JSON.
    # Productizes /tmp/dag_gantt.py prototype into kernel CLI for
    # debugging fanout balance + chain blockers.
    gantt = sub.add_parser(
        "gantt",
        help="Per-dev swim-lane Gantt + dep DAG as Mermaid markdown",
    )
    gantt.add_argument("--format", choices=["mermaid", "json"], default="mermaid")
    gantt.add_argument(
        "--only", choices=["gantt", "dag", "both"], default="both",
        help="Output only Gantt, only DAG, or both (default both)",
    )
    gantt.add_argument(
        "--state-dir", type=str, default=None,
        help="Path to runtime state dir (default: project.state_dir)",
    )
    gantt.set_defaults(func=run_gantt)

    # doc 65 P0: feature-level delivery trace / execution graph / drift,
    # all read-only projections over kanban + events + accepted task-map.
    for name, help_text in (
        ("delivery", "Feature-level idea->ship delivery trace"),
        ("execution-graph", "Planned task-map joined with actual runtime"),
        ("drift", "Planned-vs-actual drift report"),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("feature_id", help="Feature id (or '' to group all)")
        sp.add_argument("--format", choices=["table", "json"], default="table")
        sp.add_argument(
            "--task-map-ref", type=str, default="",
            help="Explicit task-map path (default: artifacts/<feature_id>/task_map.json)",
        )
        sp.add_argument("--state-dir", type=str, default=None)
        sp.set_defaults(func=run_delivery_trace, trace_view=name)

    node = sub.add_parser("task-node", help="Single task node: planned vs actual + drift")
    node.add_argument("task_id")
    node.add_argument("--format", choices=["table", "json"], default="table")
    node.add_argument("--state-dir", type=str, default=None)
    node.set_defaults(func=run_delivery_trace, trace_view="task-node")

    # doc 69 S-g: feature delivery completion report (post-mortem).
    rep = sub.add_parser("report", help="Delivery completion report (post-mortem) by feature_id")
    rep.add_argument("feature_id")
    rep.add_argument("--format", choices=["table", "json"], default="table")
    rep.add_argument("--state-dir", type=str, default=None)
    rep.set_defaults(func=run_delivery_report)

    # doc 68 S1: aggregate one fanout/workflow run (launch vs execution).
    wfr = sub.add_parser("workflow-run", help="Aggregate one fanout/workflow run by fanout_id")
    wfr.add_argument("fanout_id")
    wfr.add_argument("--format", choices=["table", "json"], default="table")
    wfr.add_argument("--state-dir", type=str, default=None)
    wfr.set_defaults(func=run_workflow_run)

    export = sub.add_parser("export", help="Export a Delivery thick trace")
    export.add_argument("target", nargs="?", default="", help="Delivery target / feature id")
    export.add_argument("--target", dest="target_opt", default="", help="Delivery target / feature id")
    export.add_argument("--format", choices=["otlp-json"], default="otlp-json")
    export.add_argument("--output", default="-", help="Output file, or '-' for stdout")
    export.add_argument("--state-dir", type=str, default=None)
    export.set_defaults(func=run_export)

    parser.set_defaults(func=lambda args: _show_help(parser))


def run_delivery_trace(args: argparse.Namespace) -> int:
    """zf trace delivery|execution-graph|drift|task-node — read-only projections."""
    from datetime import datetime, timezone

    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not context.state_dir.exists():
        print("Error: not initialized. Run 'zf init' first.", file=sys.stderr)
        return 1

    from zf.runtime.delivery_trace_resolve import resolve_delivery_trace

    view = getattr(args, "trace_view", "delivery")
    trace = resolve_delivery_trace(
        state_dir=context.state_dir,
        config=context.config,
        generated_at=datetime.now(timezone.utc).isoformat(),
        project_id=context.project_root.name,
        feature_id="" if view == "task-node" else args.feature_id,
        task_id=args.task_id if view == "task-node" else "",
        task_map_ref=getattr(args, "task_map_ref", ""),
    )

    fmt = getattr(args, "format", "table")
    if view == "execution-graph":
        return _emit(trace["execution_graph"], fmt, _render_graph, trace)
    if view == "drift":
        return _emit(trace["drift_report"], fmt, _render_drift, trace)
    if view == "task-node":
        node = next(
            (n for n in trace["execution_graph"]["nodes"]
             if n["task_id"] == args.task_id), None,
        )
        if node is None:
            print(f"Error: task {args.task_id} not found in any trace", file=sys.stderr)
            return 1
        return _emit(node, fmt, _render_node, trace)
    return _emit(trace, fmt, _render_delivery, trace)


def run_delivery_report(args: argparse.Namespace) -> int:
    """zf trace report <feature_id> — read-only delivery completion report."""
    from datetime import datetime, timezone

    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not context.state_dir.exists():
        print("Error: not initialized. Run 'zf init' first.", file=sys.stderr)
        return 1

    from zf.runtime.delivery_trace_resolve import resolve_delivery_report

    report = resolve_delivery_report(
        state_dir=context.state_dir, config=context.config,
        generated_at=datetime.now(timezone.utc).isoformat(),
        project_id=context.project_root.name, feature_id=args.feature_id,
    )
    if getattr(args, "format", "table") == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    pm = report["post_mortem"]
    fpy = pm["first_pass_yield"]
    fpy_s = f"{int(fpy * 100)}%" if fpy is not None else "-"
    dur = pm["duration_seconds"]
    print(f"Delivery Report {report['feature_id']} verdict={pm['verdict']}")
    print(f"  duration={dur if dur is not None else '-'}s  first_pass_yield={fpy_s}  "
          f"rework={pm['rework_episodes']}  paused={pm['pause_total']}")
    sh = pm["ship"]
    print(f"  ship: shipped={sh['shipped']} status={sh['ship_status'] or '-'} "
          f"merge={sh['merge_ref'] or '-'} readiness={sh['readiness']}")
    for p in pm["phase_summary"]:
        pr = p["pass_rate"]
        pr_s = f"{int(pr * 100)}%" if pr is not None else "-"
        print(f"  ▸ {p['phase_id']} [{p['status']}] 完成={int((p['completion_rate'] or 0) * 100)}% "
              f"达标={pr_s} verdict={p['verdict']} rework={p['rework_count']}")


def run_export(args: argparse.Namespace) -> int:
    """zf trace export <target> --format otlp-json — read-only exporter."""
    from datetime import datetime, timezone

    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    target = str(getattr(args, "target_opt", "") or getattr(args, "target", "") or "").strip()
    if not target:
        print("Error: trace export requires a target", file=sys.stderr)
        return 2

    from zf.runtime.delivery_thick_trace import export_otlp_json
    from zf.runtime.delivery_trace_resolve import resolve_delivery_trace

    generated_at = datetime.now(timezone.utc).isoformat()
    trace = resolve_delivery_trace(
        state_dir=context.state_dir,
        config=context.config,
        generated_at=generated_at,
        project_id=context.project_root.name,
        feature_id=target,
    )
    events = list(enumerate(event_log_from_project(
        context.state_dir, config=context.config,
    ).read_all()))
    from zf.runtime.delivery_thick_trace import build_delivery_thick_trace

    thick = build_delivery_thick_trace(
        trace=trace,
        events=events,
        generated_at=generated_at,
        project_id=context.project_root.name,
    )
    payload = json.dumps(export_otlp_json(thick), indent=2, ensure_ascii=False)
    output = str(getattr(args, "output", "-") or "-")
    if output == "-":
        print(payload)
    else:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")
    return 0


def run_workflow_run(args: argparse.Namespace) -> int:
    """zf trace workflow-run <fanout_id> — read-only fanout/workflow run view."""
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not context.state_dir.exists():
        print("Error: not initialized. Run 'zf init' first.", file=sys.stderr)
        return 1

    from zf.runtime.workflow_run import build_workflow_run

    event_log = event_log_from_project(context.state_dir, config=context.config)
    events = list(enumerate(event_log.read_all()))
    run = build_workflow_run(fanout_id=args.fanout_id, events=events)

    if getattr(args, "format", "table") == "json":
        print(json.dumps(run, indent=2, ensure_ascii=False))
        return 0
    print(f"WorkflowRun {run['fanout_id']} status={run['status']} "
          f"pattern={run.get('pattern', {}).get('pattern_id') or '-'} "
          f"topology={run.get('topology') or '-'}")
    launched = sum(1 for o in run.get("launch_outcomes", []) if o.get("dispatched"))
    print(f"  launched {launched}/{len(run.get('launch_outcomes', []))}  "
          f"executed {len(run.get('execution_outcomes', []))}")
    for o in run.get("execution_outcomes", []):
        line = f"  {o['child_id']:<16} {o['status']}"
        if o.get("reason"):
            line += f"  ({o['reason']})"
        print(line)
    for d in run.get("diagnostics", []):
        print(f"  ! {d.get('kind')}: {d.get('message')}")
    return 0


def _emit(payload, fmt, renderer, trace) -> int:
    if fmt == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        renderer(payload, trace)
    return 0


def _render_delivery(trace, _t) -> None:
    ship = trace["ship"]
    shipped = " shipped" if ship.get("shipped") else ""
    print(f"Delivery Trace {trace['feature_id'] or '(synthetic)'} "
          f"status={trace['status']} ship={ship.get('readiness', ship.get('status'))}"
          f"{shipped} drift={trace['drift_report']['status']}")
    eg = trace["execution_graph"]
    print(f"  tasks {eg['task_count']}: done={eg['done_count']} "
          f"in_progress={eg['in_progress_count']} blocked={eg['blocked_count']} "
          f"waiting={eg['waiting_count']}")
    # doc 69: phase-level rollup
    for ph in trace.get("phases", []):
        pr = ph.get("pass_rate")
        pr_s = f"{int(pr * 100)}%" if pr is not None else "-"
        extra = []
        if ph.get("rework_count"):
            extra.append(f"rework={ph['rework_count']}")
        if ph.get("paused_count"):
            extra.append(f"paused={ph['paused_count']}")
        print(f"  ▸ phase {ph['phase_id']} [{ph['status']}] "
              f"完成={int(ph['completion_rate'] * 100)}% 达标={pr_s} "
              f"verdict={ph['eval']['verdict']}"
              + (f" {' '.join(extra)}" if extra else ""))
        for run in ph.get("agent_runs", []):
            print(f"      agent-run {run['task_id']} [{run['topology'] or '-'}] "
                  f"{run['status']} launched={run['launched']}/{run['expected']} "
                  f"executed={run['executed']}")
    _render_graph(eg, trace)
    if trace["drift_report"]["items"]:
        _render_drift(trace["drift_report"], trace)


def _render_graph(eg, _t) -> None:
    for wave in eg.get("waves", []):
        print(f"Wave {wave['wave']} [{wave['status']}]")
        for tid in wave["task_ids"]:
            node = next((n for n in eg["nodes"] if n["task_id"] == tid), {})
            _print_node_line(node)
    if not eg.get("waves"):
        for node in eg.get("nodes", []):
            _print_node_line(node)


def _print_node_line(node) -> None:
    actual = node.get("actual", {})
    planned = node.get("planned", {})
    print(f"  {node.get('task_id', ''):<16} {actual.get('status', ''):<12} "
          f"owner={planned.get('owner_role', '') or '-'} "
          f"actual={actual.get('assigned_to', '') or '-'}")


def _render_drift(report, _t) -> None:
    s = report.get("summary", {})
    print(f"Drift [{report['status']}] error={s.get('error', 0)} "
          f"warning={s.get('warning', 0)} info={s.get('info', 0)}")
    for item in report.get("items", []):
        print(f"  {item['severity'].upper():<7} {item['task_id']:<16} "
              f"{item['kind']}: {item['message']}")


def _render_node(node, _t) -> None:
    _print_node_line(node)
    for item in node.get("drift", []):
        print(f"  drift: {item.get('kind', '')} {item.get('message', '')}")


def run_spans(args: argparse.Namespace) -> int:
    """ZF-OBS-SPAN-001: project events.jsonl → .zf/traces/spans.jsonl."""
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not context.state_dir.exists():
        print("Error: not initialized. Run 'zf init' first.", file=sys.stderr)
        return 1

    from zf.runtime.span_projection import (
        project_spans, write_run_trace, write_spans_jsonl,
    )

    event_log = event_log_from_project(
        context.state_dir, config=context.config,
    )
    events = event_log.read_all()
    spans = project_spans(events)
    spans_path = write_spans_jsonl(context.state_dir, spans)
    print(f"Wrote {len(spans)} spans to {spans_path}")
    run_id = getattr(args, "run_id", None)
    if run_id:
        run_path = write_run_trace(
            context.state_dir, run_id=run_id, spans=spans,
        )
        print(f"Wrote run rollup to {run_path}")
    return 0


def run_operation(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not context.state_dir.exists():
        print("Error: not initialized. Run 'zf init' first.", file=sys.stderr)
        return 1

    from zf.runtime.operation_projection import project_operation

    result = project_operation(context.state_dir, args.dispatch_id)
    if getattr(args, "format", "json") == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    print(
        f"Operation {result.get('dispatch_id', '')} "
        f"task={result.get('task_id', '') or '-'} "
        f"state={result.get('state', '') or '-'} "
        f"events={len(result.get('timeline') or [])}"
    )
    for item in result.get("timeline") or []:
        print(
            f"[{item.get('ts', '')}] {item.get('event_id', '')} "
            f"{item.get('type', '')} actor={item.get('actor', '') or '-'} "
            f"phase={item.get('phase', '') or '-'}"
        )
    return 0


def run_workflow_operation(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not context.state_dir.exists():
        print("Error: not initialized. Run 'zf init' first.", file=sys.stderr)
        return 1

    from zf.runtime.operation_projection import project_workflow_operation

    result = project_workflow_operation(context.state_dir, args.operation_id)
    if getattr(args, "format", "json") == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    print(
        f"Workflow operation {result.get('operation_id', '')} "
        f"status={result.get('status', '') or '-'} "
        f"task={result.get('task_id', '') or '-'}"
    )
    for item in result.get("timeline") or []:
        print(f"[{item.get('ts', '')}] {item.get('type', '')} {item.get('event_id', '')}")
    return 0


def _show_help(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 0


def run_show(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not context.state_dir.exists():
        print("Error: not initialized. Run 'zf init' first.", file=sys.stderr)
        return 1

    event_log = event_log_from_project(context.state_dir, config=context.config)
    result = TraceQuery(event_log).show(args.trace_id)
    if not result.events:
        print(f"Error: trace {args.trace_id} not found", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps({
            "trace_id": result.trace_id,
            "mode": result.mode,
            "event_count": len(result.events),
            "events": [asdict(event) for event in result.events],
        }, indent=2, ensure_ascii=False))
        return 0

    print(f"Trace {result.trace_id} ({result.mode}, {len(result.events)} events)")
    for event in result.events:
        actor = event.actor or "?"
        task = event.task_id or "-"
        correlation = event.correlation_id or "-"
        causation = event.causation_id or "-"
        print(
            f"[{event.ts}] {event.id} {event.type} "
            f"actor={actor} task={task} correlation={correlation} causation={causation}"
        )
    return 0


def run_record_fixture(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    from pathlib import Path

    from zf.runtime.fanout_replay import record_fanout_fixture

    event_log = event_log_from_project(context.state_dir, config=context.config)
    fixture = record_fanout_fixture(
        event_log=event_log,
        state_dir=context.state_dir,
        fanout_id=args.fanout_id,
        output_path=Path(args.output),
    )
    print(json.dumps({
        "fanout_id": fixture["fanout_id"],
        "event_count": len(fixture["events"]),
        "output": args.output,
    }, indent=2, ensure_ascii=False))
    return 0


def run_replay_fixture(args: argparse.Namespace) -> int:
    from pathlib import Path

    from zf.runtime.fanout_replay import replay_fanout_fixture

    result = replay_fanout_fixture(Path(args.fixture_path))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "matched" else 1


# ───────────────── #V — zf trace gantt ─────────────────────
# Per-dev Mermaid swim-lane Gantt + dep DAG renderer.
# Reads events.jsonl (per-task dispatch chronology),
# kanban.json (blocked_by lineage), kanban-terminal-index.json
# (archived done tasks).

_WRITER_PREFIXES = ("dev-", "arch", "critic")


def _collect_per_dev_chronology(events_path) -> dict:
    """For each task_id, return {dev, start, end, status} of first
    implementing dev assignment (writer role only)."""
    from pathlib import Path
    per_task: dict[str, dict] = {}
    if not Path(events_path).exists():
        return per_task
    for line in Path(events_path).open():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        p = e.get("payload") or {}
        task_id = p.get("task_id") or e.get("task_id") or ""
        if not task_id or not task_id.startswith("TASK-"):
            continue
        short = task_id.replace("TASK-", "")
        if e["type"] == "task.dispatched":
            assignee = p.get("assignee") or ""
            if not any(assignee.startswith(pre) for pre in _WRITER_PREFIXES):
                continue
            if short not in per_task:
                per_task[short] = {
                    "dev": assignee,
                    "start": e["ts"],
                    "end": e["ts"],
                    "status": "in_progress",
                }
        elif e["type"] == "dev.build.done":
            if short in per_task:
                per_task[short]["end"] = e["ts"]
                per_task[short]["status"] = "done"
        elif e["type"] == "dev.blocked":
            if short in per_task:
                per_task[short]["end"] = e["ts"]
                per_task[short]["status"] = "blocked"
        elif e["type"] == "task.status_changed" and p.get("to") == "done":
            if short in per_task and per_task[short]["status"] != "done":
                per_task[short]["status"] = "done"
    return per_task


def _collect_task_deps_and_status(kanban_path) -> tuple[dict, dict]:
    """Return (task_deps, task_status) from kanban.json."""
    from pathlib import Path
    deps: dict[str, list[str]] = {}
    status: dict[str, str] = {}
    if not Path(kanban_path).exists():
        return deps, status
    try:
        kanban = json.loads(Path(kanban_path).read_text())
    except json.JSONDecodeError:
        return deps, status
    for t in kanban:
        tid = (t.get("id") or "").replace("TASK-", "")
        if not tid:
            continue
        deps[tid] = [b.replace("TASK-", "") for b in (t.get("blocked_by") or [])]
        status[tid] = t.get("status", "?")
    return deps, status


def _collect_terminal_archived(terminal_index_path) -> set:
    """Return set of archived done task ids (short form)."""
    from pathlib import Path
    if not Path(terminal_index_path).exists():
        return set()
    try:
        ti = json.loads(Path(terminal_index_path).read_text())
    except json.JSONDecodeError:
        return set()
    return {k.replace("TASK-", "") for k in ti.keys()}


def _emit_mermaid_gantt(per_dev: dict) -> str:
    """Render per-task per-dev as Mermaid gantt swim lanes."""
    from collections import defaultdict
    by_dev = defaultdict(list)
    for tid, d in per_dev.items():
        by_dev[d["dev"]].append((d["start"], tid, d["end"], d["status"]))
    for dev in by_dev:
        by_dev[dev].sort()

    lines = ["```mermaid", "gantt",
             f"    title Per-dev task timeline — {len(per_dev)} task(s)",
             "    dateFormat YYYY-MM-DDTHH:mm:ss",
             "    axisFormat %H:%M"]
    for dev in sorted(by_dev.keys()):
        lines.append(f"    section {dev}")
        for start, tid, end, status in by_dev[dev]:
            marker = ""
            if status == "done":
                marker = "done, "
            elif status == "blocked":
                marker = "crit, "
            elif status == "in_progress":
                marker = "active, "
            s = start.replace("+00:00", "").replace("Z", "")
            e = end.replace("+00:00", "").replace("Z", "")
            if s == e:
                lines.append(f"    {tid} :{marker}{tid.lower()}, {s}, 1m")
            else:
                lines.append(f"    {tid} :{marker}{tid.lower()}, {s}, {e}")
    lines.append("```")
    return "\n".join(lines)


def _emit_mermaid_dag(per_dev: dict, deps: dict, status: dict,
                      archived: set) -> str:
    """Render dep graph with color-coded status nodes."""
    lines = ["```mermaid", "flowchart LR",
             "    classDef done fill:#22c55e,color:#000,stroke:#15803d",
             "    classDef inflight fill:#facc15,color:#000,stroke:#a16207",
             "    classDef blocked fill:#ef4444,color:#fff,stroke:#991b1b",
             "    classDef backlog fill:#94a3b8,color:#000,stroke:#475569",
             "    classDef archived fill:#cbd5e1,color:#475569,"
             "stroke:#94a3b8,stroke-dasharray: 3 3"]

    all_tasks: set[str] = set()
    for tid, ds in deps.items():
        all_tasks.add(tid)
        all_tasks.update(ds)
    all_tasks.update(status.keys())
    all_tasks.update(archived)

    for tid in sorted(all_tasks):
        st = status.get(tid, "unknown")
        dev_info = per_dev.get(tid, {})
        dev = dev_info.get("dev", "")
        label = tid
        if dev:
            label = f"{tid}<br>{dev}"
        if tid in archived:
            lines.append(f'    {tid}["{label}"]:::archived')
        elif st == "blocked" or dev_info.get("status") == "blocked":
            lines.append(f'    {tid}["{label}"]:::blocked')
        elif st == "in_progress":
            lines.append(f'    {tid}["{label}"]:::inflight')
        elif st == "done" or dev_info.get("status") == "done":
            lines.append(f'    {tid}["{label}"]:::done')
        else:
            lines.append(f'    {tid}["{label}"]:::backlog')

    edges_seen = set()
    for tid, ds in deps.items():
        for d in ds:
            if (d, tid) not in edges_seen:
                lines.append(f"    {d} --> {tid}")
                edges_seen.add((d, tid))
    lines.append("```")
    return "\n".join(lines)


def run_gantt(args: argparse.Namespace) -> int:
    """#V: render Mermaid Gantt + DAG for operator observability."""
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    state_dir = context.state_dir
    events_path = state_dir / "events.jsonl"
    kanban_path = state_dir / "kanban.json"
    terminal_index_path = state_dir / "kanban-terminal-index.json"

    per_dev = _collect_per_dev_chronology(events_path)
    deps, status = _collect_task_deps_and_status(kanban_path)
    archived = _collect_terminal_archived(terminal_index_path)

    if args.format == "json":
        print(json.dumps({
            "per_dev": per_dev,
            "task_deps": deps,
            "task_status": status,
            "archived": sorted(archived),
        }, indent=2, ensure_ascii=False))
        return 0

    only = getattr(args, "only", "both")
    if only in ("gantt", "both"):
        print(_emit_mermaid_gantt(per_dev))
        if only == "both":
            print()
    if only in ("dag", "both"):
        print(_emit_mermaid_dag(per_dev, deps, status, archived))
    return 0
