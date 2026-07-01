"""EVAL-KANBAN-HEALTH-001 — main audit entry (doc 43 §2.7).

Aggregates 6 dimensions into one health snapshot:

1. THROUGHPUT — task completion / failure / rework-loop counts
2. WORKFLOW COVERAGE — per-task audit (EVAL-WORKFLOW-AUDIT-001)
3. ROLE HEALTH — heartbeat freshness + completion count
4. FAILURE TAXONOMY — bucket counts (EVAL-FAILURE-TAXONOMY-001)
5. COORDINATOR — dispatch:no_action ratio (EVAL-COORDINATOR-RATIO-001)
6. METRICS SNAPSHOT — diagnostics (EVAL-METRIC-DIAGNOSTICS-001)

Output is a flat dict suitable for both md + json rendering. Markdown
renderer organises it into 6 sections + recommendations.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from zf.core.events.factory import event_log_from_project
from zf.core.metrics.collector import MetricsCollector, MetricsSnapshot
from zf.core.metrics.evaluator import (
    MetricDiagnostic,
    MetricsEvaluator,
    render_diagnostic_markdown,
)
from zf.core.task.store import TaskStore
from zf.core.workflow.topology import WorkflowEventSets


# ---------------------------------------------------------------------------
# Time window parsing (mirrors zf workflow audit)
# ---------------------------------------------------------------------------


def _parse_since(since: str | None) -> datetime | None:
    if not since:
        return None
    s = since.strip().lower()
    try:
        if s.endswith("h"):
            return datetime.now(timezone.utc) - timedelta(hours=float(s[:-1]))
        if s.endswith("d"):
            return datetime.now(timezone.utc) - timedelta(days=float(s[:-1]))
        if s.endswith("m"):
            return datetime.now(timezone.utc) - timedelta(minutes=float(s[:-1]))
    except ValueError:
        return None
    return None


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_throughput(
    tasks: list, all_events: list, rework_threshold: int = 3,
) -> dict:
    completed = sum(1 for t in tasks if t.status == "done")
    failed = sum(1 for t in tasks if t.status in ("failed", "cancelled"))
    by_task_rework: Counter[str] = Counter()
    for e in all_events:
        if e.type in ("review.rejected", "test.failed", "judge.failed"):
            tid = getattr(e, "task_id", "") or ""
            if tid:
                by_task_rework[tid] += 1
    rework_looped = [
        tid for tid, n in by_task_rework.items() if n >= rework_threshold
    ]
    return {
        "tasks_completed": completed,
        "tasks_failed": failed,
        "rework_looped_count": len(rework_looped),
        "rework_looped_tasks": rework_looped[:10],
        "rework_threshold": rework_threshold,
    }


def _build_workflow_coverage(tasks: list, all_events: list) -> dict:
    """Per-task evidence_completeness via EVAL-WORKFLOW-AUDIT-001."""
    from zf.cli.workflow import audit_task

    event_sets = WorkflowEventSets.baseline()
    active_tasks = [
        t for t in tasks
        if t.status in ("in_progress", "review", "test", "judge", "done")
    ]
    audits = [
        audit_task(t.id, all_events, event_sets) for t in active_tasks
    ]
    complete = sum(1 for a in audits if a["status"] == "complete")
    partial = sum(1 for a in audits if a["status"] == "partial")
    missing_acceptance = [
        t.id for t in active_tasks
        if not (
            getattr(getattr(t, "contract", None), "verification_tiers", None)
            or []
        )
    ]
    return {
        "audited": len(audits),
        "complete": complete,
        "partial": partial,
        "completeness_ratio": (
            complete / len(audits) if audits else 1.0
        ),
        "tasks_missing_acceptance_criteria": missing_acceptance[:10],
        "stage_order_violations": [
            (a["task_id"], a["stage_order_violations"])
            for a in audits if a.get("stage_order_violations")
        ][:10],
    }


def _build_role_health(
    config, all_events: list, cutoff: datetime | None,
) -> dict:
    roles = getattr(config, "roles", []) or []
    role_info: dict[str, dict] = {}
    now = datetime.now(timezone.utc)

    heartbeats: dict[str, str] = {}
    completions: dict[str, int] = {}
    for e in all_events:
        if e.type == "worker.heartbeat":
            instance = (
                (e.payload or {}).get("instance_id") if isinstance(e.payload, dict)
                else None
            ) or e.actor
            ts = getattr(e, "ts", "")
            if instance and ts > heartbeats.get(instance, ""):
                heartbeats[instance] = ts
        if e.type in (
            "dev.build.done", "review.approved", "test.passed",
            "judge.passed",
        ):
            actor = getattr(e, "actor", "") or ""
            role_name = actor.split("-", 1)[0] if "-" in actor else actor
            if role_name:
                completions[role_name] = completions.get(role_name, 0) + 1

    for role in roles:
        name = getattr(role, "name", "")
        if not name or name == "orchestrator":
            continue
        instance_id = getattr(role, "instance_id", "") or name
        last_hb = heartbeats.get(instance_id, "")
        idle_seconds: float | None = None
        if last_hb:
            try:
                hb_dt = datetime.fromisoformat(last_hb)
                idle_seconds = (now - hb_dt).total_seconds()
            except Exception:
                idle_seconds = None
        comp_count = completions.get(name, 0)
        warning = False
        if idle_seconds is not None and idle_seconds > 86400:
            warning = True
        elif comp_count == 0 and cutoff is not None:
            warning = True
        role_info[name] = {
            "instance_id": instance_id,
            "last_heartbeat_at": last_hb or "",
            "idle_seconds": idle_seconds,
            "completion_count": comp_count,
            "warning": warning,
        }
    return role_info


def _build_failure_taxonomy(all_events: list) -> dict:
    """Group rework_triage.completed events by taxonomy_bucket."""
    bucket_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    for e in all_events:
        if e.type != "task.rework.triage.completed":
            continue
        payload = e.payload if isinstance(e.payload, dict) else {}
        bucket = str(payload.get("taxonomy_bucket", "") or "unknown")
        classification = str(payload.get("classification", "") or "unknown")
        bucket_counts[bucket] += 1
        classification_counts[classification] += 1
    return {
        "by_bucket": dict(bucket_counts),
        "by_classification": dict(classification_counts.most_common(10)),
        "total": sum(bucket_counts.values()),
    }


def _build_coordinator(all_events: list) -> dict:
    """dispatch:no_action ratio from orchestrator.decision.recorded."""
    decision_counter: Counter[str] = Counter()
    outcome_reason_by_kind: dict[str, Counter[str]] = {}
    for e in all_events:
        if e.type != "orchestrator.decision.recorded":
            continue
        payload = e.payload if isinstance(e.payload, dict) else {}
        kind = str(payload.get("decision", "unknown"))
        decision_counter[kind] += 1
        if kind in ("no_action", "blocked", "failed"):
            reason = str(payload.get("outcome_reason", "") or "(empty)")
            outcome_reason_by_kind.setdefault(
                kind, Counter()
            )[reason] += 1

    dispatch_n = decision_counter.get("dispatch", 0)
    no_action_n = decision_counter.get("no_action", 0)
    if no_action_n == 0:
        ratio: float | None = math.inf if dispatch_n > 0 else None
        band = "n/a"
    else:
        ratio = dispatch_n / no_action_n
        if 0.5 <= ratio <= 3.0:
            band = "healthy"
        elif ratio < 0.5:
            band = "over_cautious"
        else:
            band = "over_eager"
    return {
        "total_wakes": sum(decision_counter.values()),
        "counts": dict(decision_counter),
        "dispatch_no_action_ratio": (
            None if ratio is None or ratio == math.inf else round(ratio, 3)
        ),
        "health_band": band,
        "by_outcome_reason": {
            k: dict(v) for k, v in outcome_reason_by_kind.items()
        },
    }


# ---------------------------------------------------------------------------
# Recommendations (deterministic, not LLM)
# ---------------------------------------------------------------------------


def _build_recommendations(snap: dict) -> list[str]:
    recs: list[str] = []
    wf = snap.get("workflow_coverage", {}) or {}
    if wf.get("tasks_missing_acceptance_criteria"):
        sample = wf["tasks_missing_acceptance_criteria"][0]
        recs.append(
            f"Investigate {sample} contract.acceptance_criteria gap "
            f"({len(wf['tasks_missing_acceptance_criteria'])} task(s) total)"
        )
    failure = snap.get("failure_taxonomy", {}) or {}
    buckets = failure.get("by_bucket", {}) or {}
    content_n = buckets.get("content", 0)
    infra_n = buckets.get("infra", 0)
    if content_n > 0 and content_n >= infra_n * 2 and content_n >= 3:
        recs.append(
            "Review reviewer/test prompt quality — content failures "
            f"({content_n}) dominate infra ({infra_n})"
        )
    coordinator = snap.get("coordinator", {}) or {}
    if coordinator.get("health_band") == "over_cautious":
        recs.append(
            "Orchestrator over-cautious — investigate why no_action "
            "rate is high (check outcome_reason breakdown)"
        )
    if coordinator.get("health_band") == "over_eager":
        recs.append(
            "Orchestrator over-eager — dispatching without checking "
            "preconditions; review run_once preflight"
        )
    roles = snap.get("role_health", {}) or {}
    for role_name, info in roles.items():
        if info.get("warning"):
            if info.get("idle_seconds") and info["idle_seconds"] > 86400:
                recs.append(
                    f"Consider scaling {role_name} replicas — last "
                    f"heartbeat {info['idle_seconds'] / 3600:.1f}h ago"
                )
            elif info.get("completion_count", 0) == 0:
                recs.append(
                    f"Role {role_name} has 0 completions in window — "
                    "check role dispatch path"
                )
    diags = snap.get("metric_diagnostics", []) or []
    critical_metrics = [
        d.get("metric_name") for d in diags
        if d.get("health_band") == "critical"
    ]
    if critical_metrics:
        recs.append(
            f"Metrics critical: {', '.join(critical_metrics[:3])} — "
            "run `zf metrics diagnose` for details"
        )
    return recs


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


def build_health_snapshot(
    *,
    state_dir: Path,
    config,
    since: str | None = None,
) -> dict:
    """Build the unified health snapshot. Returns a flat dict for
    json + markdown rendering."""
    cutoff = _parse_since(since)
    event_log = event_log_from_project(state_dir, config=config)
    task_store = TaskStore(state_dir / "kanban.json")

    all_events = event_log.read_all() if event_log.path.exists() else []
    if cutoff is not None:
        cutoff_iso = cutoff.isoformat()
        all_events = [e for e in all_events if (e.ts or "") >= cutoff_iso]

    tasks = task_store.list_all_with_archive()

    # MetricsSnapshot — feed downstream diagnostics
    try:
        from zf.core.cost.tracker import CostTracker

        cost = CostTracker(state_dir / "cost.jsonl")
    except Exception:
        cost = None
    try:
        metrics = MetricsCollector.compute(
            events=event_log, tasks=task_store, cost=cost,
        )
    except Exception:
        metrics = MetricsSnapshot()

    diagnostics = MetricsEvaluator().evaluate_snapshot(metrics)

    snap = {
        "window": since or "all",
        "events_considered": len(all_events),
        "tasks_total": len(tasks),
        "throughput": _build_throughput(tasks, all_events),
        "workflow_coverage": _build_workflow_coverage(tasks, all_events),
        "role_health": _build_role_health(config, all_events, cutoff),
        "failure_taxonomy": _build_failure_taxonomy(all_events),
        "coordinator": _build_coordinator(all_events),
        "metrics_snapshot": metrics.to_dict(),
        "metric_diagnostics": [
            {
                "metric_name": d.metric_name,
                "value": d.value,
                "health_band": d.health_band,
                "trend": d.trend,
                "root_cause_hints": list(d.root_cause_hints),
                "recommendations": list(d.recommendations),
            }
            for d in diagnostics
        ],
    }
    snap["recommendations"] = _build_recommendations(snap)
    return snap


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_health_md(snap: dict) -> str:
    lines: list[str] = [
        f"Kanban Health · {snap['window']} window",
        "",
    ]
    sep = "═" * 60

    # THROUGHPUT
    lines.append(sep)
    lines.append("THROUGHPUT")
    lines.append("─" * 60)
    tp = snap["throughput"]
    rework_warn = "  ⚠" if tp["rework_looped_count"] else ""
    lines.append(f"  Tasks completed:        {tp['tasks_completed']}")
    lines.append(f"  Tasks failed:           {tp['tasks_failed']}")
    lines.append(
        f"  Rework looped "
        f"(≥{tp['rework_threshold']} retries): {tp['rework_looped_count']}{rework_warn}"
    )
    if tp["rework_looped_tasks"]:
        lines.append(f"    samples: {', '.join(tp['rework_looped_tasks'][:5])}")
    lines.append(sep)

    # WORKFLOW COVERAGE
    lines.append("WORKFLOW COVERAGE")
    lines.append("─" * 60)
    wc = snap["workflow_coverage"]
    icon = "✓" if wc["completeness_ratio"] >= 0.9 else "⚠"
    lines.append(
        f"  {icon} {wc['complete']}/{wc['audited']} tasks completely covered "
        f"({wc['completeness_ratio']*100:.0f}%)"
    )
    if wc["partial"]:
        lines.append(f"  ⚠ {wc['partial']} task(s) partial coverage")
    if wc["tasks_missing_acceptance_criteria"]:
        lines.append(
            f"  ⚠ Missing acceptance_criteria: "
            f"{', '.join(wc['tasks_missing_acceptance_criteria'][:5])}"
        )
    if wc["stage_order_violations"]:
        lines.append(
            f"  ⚠ Stage order violations: "
            f"{len(wc['stage_order_violations'])} task(s)"
        )
    lines.append(sep)

    # ROLE HEALTH
    lines.append("ROLE HEALTH")
    lines.append("─" * 60)
    rh = snap["role_health"]
    if not rh:
        lines.append("  (no worker roles)")
    else:
        for role_name in sorted(rh.keys()):
            info = rh[role_name]
            icon = "⚠" if info["warning"] else "✓"
            if info["last_heartbeat_at"]:
                hb = (
                    f"last heartbeat "
                    f"{info['idle_seconds'] / 3600:.1f}h ago"
                    if info["idle_seconds"] else
                    f"last heartbeat {info['last_heartbeat_at']}"
                )
            else:
                hb = "no heartbeat"
            lines.append(
                f"  {icon} {role_name:8s}: "
                f"{info['completion_count']} completion(s), {hb}"
            )
    lines.append(sep)

    # FAILURE TAXONOMY
    lines.append("FAILURE TAXONOMY (per EVAL-FAILURE-TAXONOMY-001)")
    lines.append("─" * 60)
    ft = snap["failure_taxonomy"]
    if ft["total"] == 0:
        lines.append("  (no task.rework.triage.completed events in window)")
    else:
        for bucket in ("infra", "content", "terminal", "unknown"):
            n = ft["by_bucket"].get(bucket, 0)
            if n == 0:
                continue
            tag = {
                "infra": "(auto-retry)",
                "content": "(needs review)",
                "terminal": "(escalate)",
                "unknown": "",
            }.get(bucket, "")
            lines.append(f"  {bucket:10s}: {n} {tag}")
    lines.append(sep)

    # COORDINATOR
    lines.append("COORDINATOR (per EVAL-COORDINATOR-RATIO-001)")
    lines.append("─" * 60)
    co = snap["coordinator"]
    if co["total_wakes"] == 0:
        lines.append("  (no orchestrator.decision.recorded events in window)")
    else:
        for kind, n in sorted(co["counts"].items(), key=lambda x: -x[1]):
            lines.append(f"  {kind:12s}: {n}")
        if co["dispatch_no_action_ratio"] is not None:
            band_icons = {
                "healthy": "✓",
                "over_cautious": "⚠",
                "over_eager": "⚠",
                "n/a": "—",
            }
            icon = band_icons.get(co["health_band"], "?")
            lines.append(
                f"  {icon} dispatch:no_action = "
                f"{co['dispatch_no_action_ratio']} ({co['health_band']})"
            )
    lines.append(sep)

    # METRICS SNAPSHOT
    lines.append("METRICS SNAPSHOT (per EVAL-METRIC-DIAGNOSTICS-001)")
    lines.append("─" * 60)
    diags = snap.get("metric_diagnostics", [])
    crit = [d for d in diags if d["health_band"] == "critical"]
    warn = [d for d in diags if d["health_band"] == "warning"]
    healthy = [d for d in diags if d["health_band"] == "healthy"]
    lines.append(
        f"  ✓ healthy: {len(healthy)}    "
        f"⚠ warning: {len(warn)}    "
        f"✗ critical: {len(crit)}"
    )
    for d in crit[:5]:
        lines.append(f"  ✗ {d['metric_name']} = {d['value']}")
        for hint in d.get("root_cause_hints", [])[:1]:
            lines.append(f"    hint: {hint}")
    lines.append(sep)

    # RECOMMENDATIONS
    recs = snap.get("recommendations", [])
    lines.append("RECOMMENDATIONS")
    lines.append("─" * 60)
    if not recs:
        lines.append("  (no actions needed — system healthy)")
    else:
        for r in recs:
            lines.append(f"  - {r}")
    lines.append(sep)

    return "\n".join(lines)
