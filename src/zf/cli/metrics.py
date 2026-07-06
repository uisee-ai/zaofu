"""zf metrics — MetricsSnapshot access from the CLI.

Subcommands:
  snapshot        Compute and print the current 12-metric snapshot.
                  --format json|table (default table)
                  --diff baseline.json   (show delta vs baseline)

Example:
  zf metrics snapshot --format=json > baseline.json
  zf metrics snapshot --diff baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zf.core.cost.tracker import CostTracker
from zf.core.config.project_context import resolve_state_dir
from zf.core.events.log import EventLog
from zf.core.metrics.collector import MetricsCollector, MetricsSnapshot
from zf.core.task.store import TaskStore


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("metrics", help="Long-horizon metrics snapshot")
    sub = parser.add_subparsers(dest="metrics_cmd")

    snap = sub.add_parser("snapshot", help="Print current MetricsSnapshot")
    snap.add_argument("--format", choices=["json", "table"], default="table")
    snap.add_argument(
        "--state-dir",
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml)",
    )
    snap.add_argument("--diff", type=str, default=None,
                       help="Path to a baseline snapshot JSON")
    snap.set_defaults(func=_run_snapshot)

    # EVAL-METRIC-DIAGNOSTICS-001: diagnose every snapshot field
    diag = sub.add_parser(
        "diagnose",
        help="Run MetricsEvaluator on current snapshot (health band + trend + recommendations)",
    )
    diag.add_argument("--format", choices=["md", "json"], default="md")
    diag.add_argument("--state-dir", default=None)
    diag.add_argument(
        "--metric", default=None,
        help="Restrict output to a single metric field (e.g. mtts)",
    )
    diag.add_argument(
        "--history",
        default=None,
        help="Path to a JSON file containing a list of older MetricsSnapshot dicts (for trend detection)",
    )
    diag.set_defaults(func=_run_diagnose)

    # EVAL-COORDINATOR-RATIO-001: dispatch / no_action / blocked ratio
    ratio = sub.add_parser(
        "decision-ratio",
        help="Orchestrator decision distribution + healthy-band check",
    )
    ratio.add_argument("--format", choices=["md", "json"], default="md")
    ratio.add_argument("--state-dir", default=None)
    ratio.add_argument(
        "--by-reason", action="store_true",
        help="Group no_action / blocked decisions by outcome_reason",
    )
    ratio.set_defaults(func=_run_decision_ratio)

    # P1-12(审计 SYNTHESIS §6):S1-S5「跑稳」判据机械化
    stability = sub.add_parser(
        "stability",
        help="S1-S5 stability verdict over an events log (audit SYNTHESIS §6)",
    )
    stability.add_argument("--state-dir", default=None)
    stability.add_argument(
        "--events", default=None,
        help="Path to an events.jsonl (overrides --state-dir; archives OK)",
    )
    stability.add_argument(
        "--baseline", default=None,
        help="Baseline events.jsonl for S1 new-failure-class diff",
    )
    stability.add_argument("--format", choices=["json", "md"], default="md")
    stability.set_defaults(func=_run_stability)

    parser.set_defaults(func=lambda a: _show_help(parser))


def _show_help(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 0


def _state_dir(explicit_state_dir: str | None = None) -> Path:
    return resolve_state_dir(explicit_state_dir=explicit_state_dir)


def _compute(explicit_state_dir: str | None = None) -> MetricsSnapshot:
    sd = _state_dir(explicit_state_dir)
    events = EventLog(sd / "events.jsonl")
    tasks = TaskStore(sd / "kanban.json")
    cost = CostTracker(sd / "cost.jsonl")
    return MetricsCollector.compute(events=events, tasks=tasks, cost=cost)


def _load_history_from_path(path: Path) -> list[MetricsSnapshot]:
    """EVAL-METRIC-DIAGNOSTICS-001: load older snapshots for trend
    detection. Tolerates malformed entries by skipping them."""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    out: list[MetricsSnapshot] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            # MetricsSnapshot tolerates missing fields (all have defaults)
            out.append(MetricsSnapshot(**{
                k: v for k, v in entry.items()
                if k in MetricsSnapshot.__dataclass_fields__
            }))
        except Exception:
            continue
    return out


def _run_diagnose(args: argparse.Namespace) -> int:
    """EVAL-METRIC-DIAGNOSTICS-001: produce per-metric diagnostics."""
    from zf.core.metrics.evaluator import (
        MetricsEvaluator,
        render_diagnostic_markdown,
    )

    snap = _compute(args.state_dir)
    history: list[MetricsSnapshot] = []
    if args.history:
        history = _load_history_from_path(Path(args.history))
    diags = MetricsEvaluator().evaluate_snapshot(snap, history=history)

    if args.metric:
        diags = [d for d in diags if d.metric_name == args.metric]
        if not diags:
            print(f"Error: metric {args.metric!r} not band-able", file=sys.stderr)
            return 1

    if args.format == "json":
        print(json.dumps(
            [
                {
                    "metric_name": d.metric_name,
                    "value": d.value,
                    "health_band": d.health_band,
                    "trend": d.trend,
                    "root_cause_hints": list(d.root_cause_hints),
                    "recommendations": list(d.recommendations),
                }
                for d in diags
            ],
            indent=2,
            ensure_ascii=False,
        ))
    else:
        print(render_diagnostic_markdown(diags))
    return 0


_HEALTHY_DISPATCH_NO_ACTION_BAND = (0.5, 3.0)


def _run_decision_ratio(args: argparse.Namespace) -> int:
    """EVAL-COORDINATOR-RATIO-001: distribution of
    orchestrator.decision.recorded by decision kind, plus
    dispatch:no_action ratio health check."""
    from collections import Counter

    sd = _state_dir(args.state_dir)
    events = EventLog(sd / "events.jsonl")
    if not events.path.exists():
        print("Error: events.jsonl missing", file=sys.stderr)
        return 1
    all_events = events.read_all()
    decision_events = [
        e for e in all_events
        if e.type == "orchestrator.decision.recorded"
    ]
    if not decision_events:
        print("No orchestrator.decision.recorded events found.")
        print("(Tip: orchestrator must have run at least once + ORCH-ACT-001 must be active.)")
        return 0

    decision_counter: Counter[str] = Counter()
    by_reason: dict[str, Counter[str]] = {}
    for e in decision_events:
        payload = e.payload if isinstance(e.payload, dict) else {}
        kind = str(payload.get("decision", "unknown"))
        decision_counter[kind] += 1
        if kind in ("no_action", "blocked", "failed"):
            reason = str(payload.get("outcome_reason", "") or "(empty)")
            by_reason.setdefault(kind, Counter())[reason] += 1

    total = sum(decision_counter.values())
    dispatch_n = decision_counter.get("dispatch", 0)
    no_action_n = decision_counter.get("no_action", 0)
    ratio: float | None
    band: str
    if no_action_n == 0:
        ratio = float("inf") if dispatch_n > 0 else None
        band = "n/a"
    else:
        ratio = dispatch_n / no_action_n
        if _HEALTHY_DISPATCH_NO_ACTION_BAND[0] <= ratio <= _HEALTHY_DISPATCH_NO_ACTION_BAND[1]:
            band = "healthy"
        elif ratio < _HEALTHY_DISPATCH_NO_ACTION_BAND[0]:
            band = "over_cautious"
        else:
            band = "over_eager"

    if args.format == "json":
        out = {
            "total": total,
            "counts": dict(decision_counter),
            "dispatch_no_action_ratio": ratio,
            "health_band": band,
            "healthy_band": list(_HEALTHY_DISPATCH_NO_ACTION_BAND),
        }
        if args.by_reason:
            out["by_reason"] = {k: dict(v) for k, v in by_reason.items()}
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    print(f"Orchestrator Decision Ratio · {total} wakes\n")
    for kind, count in decision_counter.most_common():
        pct = 100 * count / total if total else 0.0
        print(f"  {kind:12s}: {count:5d} ({pct:.0f}%)")
    print()
    ratio_str = (
        "n/a" if ratio is None or ratio == float("inf") else f"{ratio:.2f}"
    )
    band_label = {
        "healthy": "✓ healthy",
        "over_cautious": "⚠ over-cautious (rarely dispatches)",
        "over_eager": "⚠ over-eager (dispatches without thinking)",
        "n/a": "n/a (insufficient data)",
    }.get(band, band)
    print(
        f"Health: dispatch:no_action = {dispatch_n}:{no_action_n} "
        f"= {ratio_str}  {band_label}"
    )
    print(
        f"  healthy band: "
        f"[{_HEALTHY_DISPATCH_NO_ACTION_BAND[0]}, "
        f"{_HEALTHY_DISPATCH_NO_ACTION_BAND[1]}]"
    )

    if args.by_reason:
        print("\nBy outcome_reason:")
        for kind in sorted(by_reason.keys()):
            counter = by_reason[kind]
            print(f"  {kind}:")
            for reason, count in counter.most_common():
                print(f"    {reason}: {count}")
    return 0


def _run_snapshot(args: argparse.Namespace) -> int:
    snap = _compute(args.state_dir)
    if args.diff:
        baseline_path = Path(args.diff)
        if not baseline_path.exists():
            print(f"Error: baseline {args.diff} not found", file=sys.stderr)
            return 1
        try:
            baseline = json.loads(baseline_path.read_text())
        except Exception as e:
            print(f"Error: baseline not JSON: {e}", file=sys.stderr)
            return 1
        _print_diff(baseline, snap)
        return 0

    if args.format == "json":
        print(json.dumps(snap.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_table(snap)
    return 0


# Friendly labels for table view (group, display name).
_ROWS: list[tuple[str, str, str]] = [
    ("A. 持续性", "MTTS", "mtts"),
    ("A. 持续性", "StuckRecoveryRate", "stuck_recovery_rate"),
    ("A. 持续性", "CrashFreeHours", "crash_free_hours"),
    ("A. 持续性", "ResumeFidelity", "resume_fidelity"),
    ("B. 对齐",  "VCR", "vcr"),
    ("B. 对齐",  "ScopeViolationRate", "scope_violation_rate"),
    ("B. 对齐",  "DiscriminatorCatchRate", "discriminator_catch_rate"),
    ("B. 对齐",  "GoalDrift", "goal_drift"),
    ("C. 进度",  "Throughput/h", "throughput_per_hour"),
    ("C. 进度",  "ReworkRatio", "rework_ratio"),
    ("C. 进度",  "CausalDepthMean", "causal_depth_mean"),
    ("C. 进度",  "MemoryHitRate", "memory_hit_rate"),
    ("D. 经济",  "Cost/Task USD", "cost_per_task"),
    ("D. 经济",  "Tokens/Task", "token_per_task"),
    ("D. 经济",  "RecycleFreq/h", "recycle_freq_per_hour"),
    ("D. 经济",  "BudgetBreachRate", "budget_breach_rate"),
]


def _print_table(snap: MetricsSnapshot) -> None:
    d = snap.to_dict()
    current_group = ""
    print(f"window_hours={snap.window_hours:.3f}  "
          f"events={snap.events_considered}  done={snap.tasks_done}")
    for group, label, key in _ROWS:
        if group != current_group:
            print(f"\n{group}")
            current_group = group
        val = d.get(key, 0)
        print(f"  {label:24s}  {val:.3f}" if isinstance(val, float)
              else f"  {label:24s}  {val}")
    if snap.alerts:
        print("\n⚠️  Alerts:")
        for a in snap.alerts:
            print(f"  - {a}")


def _print_diff(baseline: dict, snap: MetricsSnapshot) -> None:
    d = snap.to_dict()
    print(f"{'metric':28s}  {'baseline':>10s}  {'current':>10s}  {'delta':>10s}")
    print("-" * 65)
    for _group, label, key in _ROWS:
        b = float(baseline.get(key, 0) or 0)
        c = float(d.get(key, 0) or 0)
        delta = c - b
        sign = "+" if delta >= 0 else ""
        print(f"{label:28s}  {b:10.3f}  {c:10.3f}  {sign}{delta:9.3f}")


def _run_stability(args: argparse.Namespace) -> int:
    import json as _json
    from pathlib import Path as _Path

    from zf.core.events.log import EventLog
    from zf.runtime.stability_metrics import evaluate_stability

    def _load(path_str: str | None, state_dir: str | None) -> list:
        if path_str:
            return EventLog(_Path(path_str)).read_all()
        from zf.core.config.loader import load_config

        config = load_config(_Path("zf.yaml"))
        sd = _Path(state_dir) if state_dir else _Path(config.project.state_dir)
        return EventLog(sd / "events.jsonl").read_all()

    events = _load(getattr(args, "events", None), getattr(args, "state_dir", None))
    baseline = None
    if getattr(args, "baseline", None):
        baseline = EventLog(_Path(args.baseline)).read_all()
    report = evaluate_stability(events, baseline_events=baseline)
    data = report.to_dict()
    if args.format == "json":
        print(_json.dumps(data, ensure_ascii=False, indent=1))
    else:
        verdict = "STABLE" if data["stable"] else "NOT STABLE"
        print(f"stability: {verdict}")
        print(f"  S1 new failure classes: {data['s1']['new_failure_classes'] or '-'}"
              f" (pass={data['s1']['pass']})")
        print(f"  S2 interventions: {data['s2']['interventions']} in "
              f"{data['s2']['window_hours']}h (pass={data['s2']['pass']})")
        print(f"  S3 unacked escalates: {data['s3']['unacked']}/{data['s3']['total']}"
              f" (pass={data['s3']['pass']})")
        print(f"  S4 stall recovery p95: {data['s4']['recovery_p95_s']}s "
              f"({data['s4']['samples']} samples)")
        print(f"  S5 blackouts/env failures: {data['s5']['blackouts']}/"
              f"{data['s5']['env_failures']} (pass={data['s5']['pass']})")
    return 0 if data["stable"] else 1
