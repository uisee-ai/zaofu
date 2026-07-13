"""Project Automation projection.

Automations are reports/alerts/proposals derived from Project runtime truth.
They are not a scheduler and do not mutate task state.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from zf.core.cost.tracker import CostSummary
from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.errors.taxonomy import classify
from zf.core.security.redaction import redact_obj
from zf.core.task.kanban_projection import KANBAN_COLUMN_OPTIONS, kanban_column_projection
from zf.core.task.lifecycle import derive_phase
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.automation_metrics import (
    build_archetype_matrix,
    commit_counts_by_task,
    cross_cutting_scorecard,
    percentile,
)


AUTOMATIONS = ("daily-brief", "weekly-review", "project-monitor")


def project_automations(
    state_dir: Path,
    *,
    project_id: str,
    project_name: str = "",
    events: list | None = None,
) -> dict[str, Any]:
    state_dir = Path(state_dir)
    if events is None:
        events = EventLog(state_dir / "events.jsonl").read_days(14)
    runs = _automation_runs(events, project_id=project_id)
    by_automation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        by_automation[str(run.get("automation_id") or "")].append(run)

    items = []
    for automation_id in AUTOMATIONS:
        all_runs = by_automation.get(automation_id, [])
        recent_runs = by_automation.get(automation_id, [])[-10:]
        run_counts_by_day = _automation_run_counts_by_day(
            events,
            automation_id=automation_id,
            project_id=project_id,
        )
        items.append({
            "automation_id": automation_id,
            "project_id": project_id,
            "title": _automation_title(automation_id),
            "status": recent_runs[-1]["status"] if recent_runs else "idle",
            "trigger": "manual|schedule|event-window",
            "window": _automation_window(automation_id),
            "last_run": recent_runs[-1] if recent_runs else None,
            "next_run": None,
            "source_events": _automation_source_events(
                events,
                automation_id=automation_id,
                project_id=project_id,
            ),
            "all_runs": all_runs,
            "recent_runs": recent_runs,
            "outputs": _default_outputs(
                automation_id,
                state_dir=state_dir,
                events=events,
                project_id=project_id,
            ),
            "proposals": _proposals(events, automation_id=automation_id, project_id=project_id),
            "run_counts_by_day": run_counts_by_day,
            "run_counts_summary": _automation_run_counts_summary(run_counts_by_day),
        })
    return {
        "schema_version": "project_automation.v1",
        "project_id": project_id,
        "project_name": project_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "automations": items,
        "items": items,
    }


def _automation_runs(events: list[ZfEvent], *, project_id: str) -> list[dict[str, Any]]:
    by_run: dict[str, dict[str, Any]] = {}
    for event in events:
        if not event.type.startswith("automation.run."):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("project_id") or "") not in {"", project_id}:
            continue
        run_id = str(payload.get("run_id") or event.id)
        row = by_run.setdefault(run_id, {
            "run_id": run_id,
            "automation_id": str(payload.get("automation_id") or ""),
            "project_id": str(payload.get("project_id") or project_id),
            "status": "running",
            "trigger": str(payload.get("trigger") or ""),
            "started_event_id": "",
            "completed_event_id": "",
            "failure_reason": "",
            "source_events": [],
        })
        row["automation_id"] = str(payload.get("automation_id") or row["automation_id"])
        row["project_id"] = str(payload.get("project_id") or row["project_id"])
        row["source_events"].append(event.id)
        if event.type == "automation.run.started":
            row["status"] = "running"
            row["started_event_id"] = event.id
        elif event.type == "automation.run.completed":
            row["status"] = str(payload.get("status") or "completed")
            row["completed_event_id"] = event.id
            row["outputs"] = _compact_run_outputs(payload.get("outputs") or [])
        elif event.type == "automation.run.failed":
            row["status"] = "failed"
            row["failure_reason"] = str(payload.get("reason") or "")
        elif event.type == "automation.run.skipped":
            row["status"] = "skipped"
            row["failure_reason"] = str(payload.get("reason") or "")
    return sorted(by_run.values(), key=lambda row: row.get("run_id", ""))


def _automation_window(automation_id: str) -> str:
    return {
        "daily-brief": "1d",
        "weekly-review": "7d",
        "project-monitor": "14d",
    }.get(automation_id, "event-window")


def _compact_run_outputs(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for output in value[:10]:
        if not isinstance(output, dict):
            continue
        compact: dict[str, Any] = {
            "type": str(output.get("type") or "report"),
            "summary": str(output.get("summary") or ""),
        }
        for key in ("project_id", "window"):
            if output.get(key):
                compact[key] = str(output.get(key))
        refs = _compact_output_refs(output)
        if refs:
            compact["refs"] = refs
        rows.append(compact)
    return redact_obj(rows)


_OUTPUT_REF_LIST_FIELDS = (
    "preview_refs",
    "report_refs",
    "artifact_refs",
    "evidence_refs",
    "source_refs",
    "task_refs",
    "trace_refs",
    "event_refs",
    "diff_refs",
    "test_refs",
    "log_refs",
    "artifacts",
    "attachments",
)

_OUTPUT_REF_SCALAR_FIELDS = (
    "task_id",
    "trace_id",
    "event_id",
    "source_event_id",
    "report_id",
    "artifact_id",
    "path",
    "file",
)

_REF_ITEM_FIELDS = (
    "kind",
    "type",
    "name",
    "path",
    "file",
    "filename",
    "task_id",
    "trace_id",
    "event_id",
    "source_event_id",
    "report_id",
    "artifact_id",
    "proposal_id",
    "mime",
    "content_type",
    "size",
    "bytes",
    "sha256",
    "summary",
    "reason",
    "actor",
    "ts",
)


def _compact_output_refs(output: dict[str, Any], *, limit: int = 20) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    nested = output.get("refs") if isinstance(output.get("refs"), dict) else {}
    carriers = [output, nested]
    for carrier in carriers:
        for key in _OUTPUT_REF_LIST_FIELDS:
            compact = _compact_ref_list(carrier.get(key), limit=limit)
            if compact:
                refs[key] = compact
        for key in _OUTPUT_REF_SCALAR_FIELDS:
            value = str(carrier.get(key) or "").strip()
            if value:
                refs.setdefault(key, value)
    return redact_obj(refs)


def _compact_ref_list(value: object, *, limit: int = 50) -> list[Any]:
    if not isinstance(value, list):
        return []
    rows: list[Any] = []
    seen: set[str] = set()
    for item in value:
        compact = _compact_ref_item(item)
        if not compact:
            continue
        key = str(compact)
        if key in seen:
            continue
        seen.add(key)
        rows.append(compact)
        if len(rows) >= limit:
            break
    return rows


def _compact_ref_item(item: object) -> Any:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return None
    compact = {
        key: item[key]
        for key in _REF_ITEM_FIELDS
        if item.get(key) not in (None, "")
    }
    return compact or None


def _automation_source_events(
    events: list[ZfEvent],
    *,
    automation_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("automation_id") or "") != automation_id:
            continue
        if str(payload.get("project_id") or "") not in {"", project_id}:
            continue
        if not (
            event.type.startswith("automation.run.")
            or event.type.startswith("automation.proposal.")
            or event.type.startswith("automation.alert.")
        ):
            continue
        refs.append(_event_ref(event))
    return refs[-30:]


def _automation_run_counts_by_day(
    events: list[ZfEvent],
    *,
    automation_id: str,
    project_id: str,
    days: int = 14,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    by_day: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        if not event.type.startswith("automation.run."):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("automation_id") or "") != automation_id:
            continue
        if str(payload.get("project_id") or "") not in {"", project_id}:
            continue
        event_ts = _parse_ts(event.ts)
        if event_ts is not None and event_ts < cutoff:
            continue
        date_key = event_ts.date().isoformat() if event_ts is not None else "unknown"
        status = event.type.rsplit(".", 1)[-1]
        if status not in {"started", "completed", "failed", "skipped"}:
            continue
        row = by_day[date_key]
        row[status] += 1
        row["events_total"] += 1
        if status in {"completed", "failed", "skipped"}:
            row["terminal_total"] += 1

    rows: list[dict[str, Any]] = []
    for date_key in sorted(by_day):
        counts = by_day[date_key]
        terminal_total = counts["terminal_total"]
        success_rate = (
            round(counts["completed"] / terminal_total, 6)
            if terminal_total else None
        )
        rows.append({
            "date": date_key,
            "started": counts["started"],
            "completed": counts["completed"],
            "failed": counts["failed"],
            "skipped": counts["skipped"],
            "terminal_total": terminal_total,
            "events_total": counts["events_total"],
            "success_rate": success_rate,
        })
    return rows


def _automation_run_counts_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    for row in rows:
        for key in (
            "started",
            "completed",
            "failed",
            "skipped",
            "terminal_total",
            "events_total",
        ):
            totals[key] += int(row.get(key) or 0)
    terminal_total = totals["terminal_total"]
    return {
        "days": len(rows),
        "started": totals["started"],
        "completed": totals["completed"],
        "failed": totals["failed"],
        "skipped": totals["skipped"],
        "terminal_total": terminal_total,
        "events_total": totals["events_total"],
        "success_rate": (
            round(totals["completed"] / terminal_total, 6)
            if terminal_total else None
        ),
    }


def _proposals(
    events: list[ZfEvent],
    *,
    automation_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        if not event.type.startswith("automation.proposal."):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("automation_id") or "") != automation_id:
            continue
        if str(payload.get("project_id") or "") not in {"", project_id}:
            continue
        rows.append({
            "event_id": event.id,
            "type": event.type,
            "proposal_id": str(payload.get("proposal_id") or ""),
            "summary": str(payload.get("summary") or payload.get("reason") or ""),
            "action": str(payload.get("action") or ""),
            "payload": redact_obj(payload.get("payload") or {}),
        })
    return rows[-20:]


def _default_outputs(
    automation_id: str,
    *,
    state_dir: Path,
    events: list[ZfEvent],
    project_id: str,
) -> list[dict[str, Any]]:
    if automation_id == "daily-brief":
        return [_daily_brief(state_dir, events=events, project_id=project_id)]
    if automation_id == "weekly-review":
        return [_weekly_review(state_dir, events=events, project_id=project_id)]
    return [_project_monitor(state_dir, events=events, project_id=project_id)]


def _daily_brief(
    state_dir: Path,
    *,
    events: list[ZfEvent],
    project_id: str,
) -> dict[str, Any]:
    recent_events = _events_in_window(events, days=1)
    store = TaskStore(state_dir / "kanban.json")
    active_tasks = store.list_all()
    tasks = store.list_all_with_archive(last_days=1)
    status_counts = _task_status_counts(tasks)
    board_counts = _task_board_counts(tasks, events, ready_ids=_ready_ids(store))
    active = [
        task.id for task in active_tasks
        if task.status not in {"done", "cancelled"}
    ]
    blocked = [task.id for task in active_tasks if task.status == "blocked"]
    done = [task.id for task in tasks if task.status == "done"]
    cancelled = [task.id for task in tasks if task.status == "cancelled"]
    failed_events = [
        event for event in recent_events
        if _is_failure_event(event)
    ][-20:]
    proposals = _pending_proposals(recent_events, project_id=project_id)
    worker_health = _worker_health(state_dir, recent_events)
    channel_attention = _channel_attention(recent_events)
    cost = _cost_by_role(state_dir, last_days=1)
    failure_event_refs = [_event_ref(event) for event in failed_events]
    decision_panel = _daily_decision_panel(recent_events, proposals=proposals)
    insights = _daily_insights(
        active=active,
        done=done,
        cancelled=cancelled,
        blocked=blocked,
        failure_events=failure_event_refs,
        worker_health=worker_health,
        channel_attention=channel_attention,
        proposals=proposals,
        cost=cost,
    )
    insights.extend(_decision_panel_insights(decision_panel))
    summary_parts = [
        f"{len(active)} active",
        f"{len(done)} done",
        f"{len(cancelled)} cancelled",
        f"{len(blocked)} blocked",
        f"{len(failed_events)} recent failures",
        f"{len(proposals)} pending proposals",
    ]
    return {
        "type": "report",
        "project_id": project_id,
        "window": "1d",
        "summary": ", ".join(summary_parts),
        "task_counts": status_counts,
        "board_counts": board_counts,
        "active_tasks": active,
        "done_tasks": done[-20:],
        "cancelled_tasks": cancelled[-20:],
        "blocked_tasks": blocked,
        "failed_events": [event.id for event in failed_events],
        "failure_events": failure_event_refs,
        "worker_health": worker_health,
        "channel_attention": channel_attention,
        "pending_proposals": proposals,
        "decision_panel": decision_panel,
        "token_context_cost": redact_obj(cost),
        "insights": insights,
        "refs": _report_preview_refs(
            task_ids=[*active, *blocked, *done[-20:]],
            event_refs=failure_event_refs,
            proposal_refs=proposals,
        ),
    }


_DECISION_ESCALATION_TYPES = frozenset({
    "human.escalate", "runtime.attention.escalated",
})


def _oldest_age_hours(events: list[ZfEvent]) -> float | None:
    now = datetime.now(timezone.utc)
    ages = [
        (now - ts).total_seconds() / 3600
        for ts in (_parse_ts(event.ts) for event in events)
        if ts is not None
    ]
    return round(max(ages), 2) if ages else None


def _daily_decision_panel(
    events: list[ZfEvent],
    *,
    proposals: list[dict[str, Any]],
) -> dict[str, Any]:
    """Items that may need an operator/owner decision today.

    Window-scoped signal counts (escalation / replan-awaiting / pending
    proposal). Ack-correlation to drop already-resolved items is a P1.5
    refinement; counts are clearly labelled as the decision *surface*.
    """
    escalations = [e for e in events if e.type in _DECISION_ESCALATION_TYPES]
    replan_awaiting = [e for e in events if e.type == "replan.adoption.awaiting_owner"]
    return {
        "escalations_awaiting": {
            "count": len(escalations),
            "oldest_age_hours": _oldest_age_hours(escalations),
            "refs": [_event_ref(event) for event in escalations[-20:]],
        },
        "replan_awaiting_owner": {
            "count": len(replan_awaiting),
            "refs": [_event_ref(event) for event in replan_awaiting[-20:]],
        },
        "proposals_pending": {"count": len(proposals)},
        "total": len(escalations) + len(replan_awaiting) + len(proposals),
    }


def _decision_panel_insights(panel: dict[str, Any]) -> list[dict[str, Any]]:
    if not panel.get("total"):
        return []
    escalations = panel["escalations_awaiting"]
    age = escalations.get("oldest_age_hours")
    return [_insight(
        "daily-decision-panel",
        severity="warn" if escalations["count"] else "info",
        category="proposals",
        title="Decisions waiting on you",
        summary=(
            f"{panel['total']} item(s) await a decision: "
            f"{escalations['count']} escalation(s)"
            + (f" (oldest {age}h)" if age else "")
            + f", {panel['replan_awaiting_owner']['count']} replan, "
            f"{panel['proposals_pending']['count']} proposal(s)."
        ),
        metric=panel["total"],
    )]


def _weekly_review(
    state_dir: Path,
    *,
    events: list[ZfEvent],
    project_id: str,
) -> dict[str, Any]:
    recent_events = _events_in_window(events, days=7)
    comparison_events = _events_in_window(events, days=14)
    store = TaskStore(state_dir / "kanban.json")
    tasks = store.list_all_with_archive(last_days=7)
    comparison_tasks = store.list_all_with_archive(last_days=14)
    status_counts = _task_status_counts(tasks)
    board_counts = _task_board_counts(tasks, events, ready_ids=_ready_ids(store))
    done = [task.id for task in tasks if task.status == "done"]
    cancelled = [task.id for task in tasks if task.status == "cancelled"]
    rework = [
        event for event in recent_events
        if "rework" in event.type or event.type in {
            "review.rejected",
            "test.failed",
            "judge.failed",
            "discriminator.failed",
            "gate.failed",
        }
    ][-50:]
    failure_taxonomy = _failure_taxonomy(recent_events)
    backlog_drift = _backlog_drift(recent_events, status_counts)
    delivery_metrics = _delivery_metrics(tasks)
    delivery_metrics_14d = _delivery_metrics(comparison_tasks)
    cost_trend = _cost_trend(state_dir, days=7)
    proposals = _proposal_status_summary(recent_events, project_id=project_id)
    rework_refs = [_event_ref(event) for event in rework]
    scorecard = cross_cutting_scorecard(recent_events)
    rework_task_ids = {event.task_id for event in rework if event.task_id}
    archetype_matrix = build_archetype_matrix(
        tasks,
        recent_events,
        duration_hours=_task_duration_hours,
        rework_task_ids=rework_task_ids,
        commit_counts=commit_counts_by_task(recent_events),
    )
    insights = _weekly_insights(
        done=done,
        cancelled=cancelled,
        rework_refs=rework_refs,
        failure_taxonomy=failure_taxonomy,
        delivery_metrics=delivery_metrics,
        cost_trend=cost_trend,
        backlog_drift=backlog_drift,
        proposals=proposals,
    )
    insights.extend(_scorecard_insights(scorecard, recent_events))
    return {
        "type": "report",
        "project_id": project_id,
        "window": "7d",
        "comparison_window": "14d",
        "summary": (
            f"{len(done)} done tasks, {len(cancelled)} cancelled tasks, "
            f"{len(rework)} rework signals"
        ),
        "task_counts": status_counts,
        "board_counts": board_counts,
        "done_tasks": done,
        "cancelled_tasks": cancelled,
        "rework_events": [event.id for event in rework],
        "rework_event_refs": rework_refs,
        "failure_taxonomy": failure_taxonomy,
        "failure_taxonomy_14d": _failure_taxonomy(comparison_events),
        "cost_trend": cost_trend,
        "backlog_drift": backlog_drift,
        "delivery_metrics": delivery_metrics,
        "delivery_metrics_14d": delivery_metrics_14d,
        "scorecard": scorecard,
        "archetype_matrix": archetype_matrix,
        "proposal_outcomes": proposals,
        "insights": insights,
        "refs": _report_preview_refs(
            task_ids=[*done[-30:], *cancelled[-10:]],
            event_refs=rework_refs,
        ),
    }


def _project_monitor(
    state_dir: Path,
    *,
    events: list[ZfEvent],
    project_id: str,
) -> dict[str, Any]:
    recent_events = _events_in_window(events, days=14)
    alert_types = {
        "worker.stuck",
        "worker.stuck.recovery_failed",
        "dispatch.silent_stall",
        "worker.context.warning",
        "worker.context.critical",
        "ship.blocked",
        "task.done.blocked",
        "cost.budget.exceeded",
        "agent.timeout",
        "agent.api_blocked",
        "provider.health.changed",
        "provider.cooldown.started",
        "provider.account.exhausted",
    }
    channel_attention = _channel_attention(recent_events)
    worker_health = _worker_health(state_dir, recent_events)
    alerts = [
        _event_ref(event)
        for event in recent_events
        if _is_project_monitor_alert(event, alert_types)
    ][-30:]
    progress_alerts = _no_progress_alerts(recent_events)
    channel_alerts = (
        channel_attention["failed_replies"]
        + channel_attention["pending_replies"]
        + channel_attention["rejected_workflows"]
        + channel_attention["pending_workflows"]
        + channel_attention["delivery_failures"]
    )[-30:]
    open_proposals = _pending_proposals(recent_events, project_id=project_id)
    store = TaskStore(state_dir / "kanban.json")
    board_counts = _task_board_counts(store.list_all(), events, ready_ids=_ready_ids(store))
    total_alerts = len(alerts) + len(progress_alerts) + len(channel_alerts)
    insights = _monitor_insights(
        alerts=alerts,
        progress_alerts=progress_alerts,
        channel_alerts=channel_alerts,
        worker_health=worker_health,
        open_proposals=open_proposals,
    )
    return {
        "type": "alert" if total_alerts else "report",
        "project_id": project_id,
        "window": "14d",
        "summary": f"{total_alerts} monitor alerts in recent window",
        "alerts": alerts,
        "progress_alerts": progress_alerts,
        "channel_alerts": channel_alerts,
        "worker_health": worker_health,
        "open_proposals": open_proposals,
        "board_counts": board_counts,
        "proposal_policy": "proposal-only",
        "insights": insights,
        "refs": _report_preview_refs(
            event_refs=[*alerts, *progress_alerts, *channel_alerts],
            proposal_refs=open_proposals,
        ),
    }


def _events_in_window(events: list[ZfEvent], *, days: int) -> list[ZfEvent]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for event in events:
        event_ts = _parse_ts(event.ts)
        if event_ts is None or event_ts >= cutoff:
            out.append(event)
    return out


def _daily_insights(
    *,
    active: list[str],
    done: list[str],
    cancelled: list[str],
    blocked: list[str],
    failure_events: list[dict[str, Any]],
    worker_health: dict[str, Any],
    channel_attention: dict[str, Any],
    proposals: list[dict[str, Any]],
    cost: dict[str, Any],
) -> list[dict[str, Any]]:
    insights: list[dict[str, Any]] = []
    if blocked:
        insights.append(_insight(
            "daily-blocked-tasks",
            severity="warn",
            category="tasks",
            title="Blocked work needs triage",
            summary=f"{len(blocked)} blocked task(s) in the active board.",
            metric=len(blocked),
            task_ids=blocked[:20],
            suggested_action="Open blocked tasks and decide unblock, defer, or reassign.",
        ))
    if failure_events:
        insights.append(_insight(
            "daily-failure-events",
            severity="critical" if len(failure_events) >= 5 else "warn",
            category="quality",
            title="Recent failure signals",
            summary=f"{len(failure_events)} failure/rejection event(s) in the last 24h.",
            metric=len(failure_events),
            source_events=[str(event.get("event_id") or "") for event in failure_events],
            suggested_action="Inspect top failure refs before dispatching more work.",
        ))
    attention_count = int(worker_health.get("attention_count") or 0)
    if attention_count:
        insights.append(_insight(
            "daily-worker-attention",
            severity="critical" if attention_count >= 5 else "warn",
            category="agents",
            title="Worker health needs attention",
            summary=f"{attention_count} worker/context/stuck signal(s) require operator review.",
            metric=attention_count,
            source_events=_worker_attention_event_ids(worker_health),
            suggested_action="Check stale workers, context pressure, and stuck recovery state.",
        ))
    channel_counts = channel_attention.get("summary") or {}
    channel_total = sum(int(channel_counts.get(key) or 0) for key in (
        "failed_replies",
        "pending_replies",
        "rejected_workflows",
        "pending_workflows",
        "delivery_failures",
    ))
    if channel_total:
        insights.append(_insight(
            "daily-channel-attention",
            severity="warn",
            category="channels",
            title="Channel work is pending",
            summary=f"{channel_total} channel/workflow attention item(s) are open.",
            metric=channel_total,
            source_events=_channel_attention_event_ids(channel_attention),
            suggested_action="Drain replies or resolve rejected workflow requests.",
        ))
    if proposals:
        insights.append(_insight(
            "daily-pending-proposals",
            severity="info",
            category="proposals",
            title="Pending automation proposals",
            summary=f"{len(proposals)} proposal(s) are waiting for operator decision.",
            metric=len(proposals),
            source_events=[str(row.get("event_id") or "") for row in proposals],
            suggested_action="Review proposals and accept/reject through controlled actions.",
        ))
    cost_total = round(sum(float(row.get("usd") or 0.0) for row in cost.values()), 6)
    if cost_total > 0:
        insights.append(_insight(
            "daily-token-cost",
            severity="info",
            category="cost",
            title="Token cost observed",
            summary=f"${cost_total:.6f} estimated cost across {len(cost)} role(s) in the last 24h.",
            metric=cost_total,
        ))
    if not insights:
        insights.append(_insight(
            "daily-clear",
            severity="ok",
            category="summary",
            title="No immediate automation concerns",
            summary=f"{len(active)} active, {len(done)} done, {len(cancelled)} cancelled.",
            metric=len(active),
        ))
    return insights[:8]


def _weekly_insights(
    *,
    done: list[str],
    cancelled: list[str],
    rework_refs: list[dict[str, Any]],
    failure_taxonomy: dict[str, Any],
    delivery_metrics: dict[str, Any],
    cost_trend: dict[str, Any],
    backlog_drift: dict[str, Any],
    proposals: dict[str, Any],
) -> list[dict[str, Any]]:
    insights = [
        _insight(
            "weekly-throughput",
            severity="ok" if done else "info",
            category="delivery",
            title="Weekly throughput",
            summary=f"{len(done)} done task(s), {len(cancelled)} cancelled task(s).",
            metric=len(done),
            task_ids=done[:20],
        ),
    ]
    terminal_rate = delivery_metrics.get("terminal_success_rate")
    if terminal_rate is not None and float(terminal_rate) < 0.8:
        insights.append(_insight(
            "weekly-low-success-rate",
            severity="warn",
            category="delivery",
            title="Terminal success rate is low",
            summary=f"Terminal success rate is {float(terminal_rate):.2f}.",
            metric=float(terminal_rate),
            suggested_action="Review cancelled/reworked tasks before expanding scope.",
        ))
    if rework_refs:
        insights.append(_insight(
            "weekly-rework",
            severity="warn",
            category="quality",
            title="Rework signals detected",
            summary=f"{len(rework_refs)} rework/rejection signal(s) in the last 7d.",
            metric=len(rework_refs),
            source_events=[str(event.get("event_id") or "") for event in rework_refs],
        ))
    failure_counts = failure_taxonomy.get("counts") or {}
    if failure_counts:
        top_failure, top_count = max(
            ((str(key), int(value or 0)) for key, value in failure_counts.items()),
            key=lambda item: item[1],
        )
        insights.append(_insight(
            "weekly-top-failure",
            severity="warn",
            category="quality",
            title="Top failure taxonomy",
            summary=f"{top_failure} is the leading failure class ({top_count}).",
            metric=top_count,
        ))
    drift_total = int(backlog_drift.get("created_events") or 0)
    if drift_total:
        insights.append(_insight(
            "weekly-backlog-drift",
            severity="info",
            category="planning",
            title="Backlog changed this week",
            summary=f"{drift_total} task creation event(s) observed in backlog drift.",
            metric=drift_total,
        ))
    proposal_counts = proposals.get("counts") if isinstance(proposals, dict) else {}
    pending = int((proposal_counts or {}).get("created") or 0)
    if pending:
        insights.append(_insight(
            "weekly-proposals",
            severity="info",
            category="proposals",
            title="Automation proposals pending",
            summary=f"{pending} proposal(s) remain in created state.",
            metric=pending,
        ))
    if int(cost_trend.get("entries") or 0):
        insights.append(_insight(
            "weekly-cost",
            severity="info",
            category="cost",
            title="Weekly cost trend",
            summary=(
                f"${float(cost_trend.get('total_usd') or 0.0):.6f} across "
                f"{int(cost_trend.get('entries') or 0)} cost entries."
            ),
            metric=float(cost_trend.get("total_usd") or 0.0),
        ))
    return insights[:8]


def _monitor_insights(
    *,
    alerts: list[dict[str, Any]],
    progress_alerts: list[dict[str, Any]],
    channel_alerts: list[dict[str, Any]],
    worker_health: dict[str, Any],
    open_proposals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    insights: list[dict[str, Any]] = []
    if alerts:
        insights.append(_insight(
            "monitor-runtime-alerts",
            severity="critical" if len(alerts) >= 5 else "warn",
            category="runtime",
            title="Runtime alerts active",
            summary=f"{len(alerts)} runtime alert signal(s) in the monitor window.",
            metric=len(alerts),
            source_events=[str(alert.get("event_id") or "") for alert in alerts],
            suggested_action="Inspect stuck/context/provider/budget alerts before more dispatch.",
        ))
    if progress_alerts:
        insights.append(_insight(
            "monitor-no-progress",
            severity="warn",
            category="progress",
            title="No progress signal",
            summary=str(progress_alerts[-1].get("reason") or "No progress observed."),
            metric=len(progress_alerts),
        ))
    if channel_alerts:
        insights.append(_insight(
            "monitor-channel-alerts",
            severity="warn",
            category="channels",
            title="Channel alerts active",
            summary=f"{len(channel_alerts)} channel attention item(s) remain open.",
            metric=len(channel_alerts),
            source_events=[
                str(row.get("event_id") or row.get("request_event_id") or "")
                for row in channel_alerts
            ],
        ))
    attention_count = int(worker_health.get("attention_count") or 0)
    if attention_count:
        insights.append(_insight(
            "monitor-worker-attention",
            severity="critical" if attention_count >= 5 else "warn",
            category="agents",
            title="Workers need operator attention",
            summary=f"{attention_count} worker health signal(s) are active.",
            metric=attention_count,
            source_events=_worker_attention_event_ids(worker_health),
        ))
    if open_proposals:
        insights.append(_insight(
            "monitor-open-proposals",
            severity="info",
            category="proposals",
            title="Open proposals available",
            summary=f"{len(open_proposals)} proposal(s) can be reviewed.",
            metric=len(open_proposals),
            source_events=[str(row.get("event_id") or "") for row in open_proposals],
        ))
    if not insights:
        insights.append(_insight(
            "monitor-clear",
            severity="ok",
            category="summary",
            title="Monitor window is quiet",
            summary="No runtime, channel, progress, or worker alerts are active.",
            metric=0,
        ))
    return insights[:8]


def _insight(
    insight_id: str,
    *,
    severity: str,
    category: str,
    title: str,
    summary: str,
    metric: int | float | None = None,
    source_events: list[str] | None = None,
    task_ids: list[str] | None = None,
    suggested_action: str = "",
) -> dict[str, Any]:
    return redact_obj({
        "id": insight_id,
        "severity": severity,
        "category": category,
        "title": title,
        "summary": summary,
        "metric": metric,
        "source_events": [value for value in (source_events or []) if value],
        "task_ids": [value for value in (task_ids or []) if value],
        "suggested_action": suggested_action,
    })


def _worker_attention_event_ids(worker_health: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("context_warnings", "context_criticals", "stuck_events"):
        rows = worker_health.get(key)
        if not isinstance(rows, list):
            continue
        refs.extend(str(row.get("event_id") or "") for row in rows if isinstance(row, dict))
    return [value for value in refs if value]


def _channel_attention_event_ids(channel_attention: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in (
        "failed_replies",
        "pending_replies",
        "rejected_workflows",
        "pending_workflows",
        "delivery_failures",
    ):
        rows = channel_attention.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            refs.append(str(row.get("event_id") or row.get("request_event_id") or ""))
    return [value for value in refs if value]


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _task_status_counts(tasks: list[Task]) -> dict[str, int]:
    counts = Counter(task.status or "unknown" for task in tasks)
    return dict(sorted(counts.items()))


def _task_board_counts(
    tasks: list[Task],
    events: list[ZfEvent],
    *,
    ready_ids: set[str],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for task in tasks:
        projection = kanban_column_projection(
            task,
            phase=derive_phase(task, events) if events else None,
            ready=task.id in ready_ids,
        )
        counts[projection.label] += 1
    return {label: int(counts.get(label) or 0) for label in KANBAN_COLUMN_OPTIONS}


def _ready_ids(store: TaskStore) -> set[str]:
    try:
        return {task.id for task in store.ready()}
    except Exception:
        return set()


def _is_failure_event(event: ZfEvent) -> bool:
    if classify(event) is not None:
        return True
    return (
        event.type.endswith(".failed")
        or event.type.endswith(".rejected")
        or event.type in {"ship.blocked", "task.done.blocked"}
    )


def _is_project_monitor_alert(event: ZfEvent, alert_types: set[str]) -> bool:
    if event.type not in alert_types and not _is_runtime_conflict_event(event):
        return False
    if event.type == "provider.health.changed":
        payload = event.payload if isinstance(event.payload, dict) else {}
        status = str(payload.get("status") or "").lower()
        reason = str(payload.get("reason") or "").lower()
        return status not in {"healthy", "recovered"} and reason not in {"healthy", "recovered"}
    return True


def _is_runtime_conflict_event(event: ZfEvent) -> bool:
    event_type = event.type.lower()
    if "conflict" not in event_type and "collision" not in event_type:
        return False
    return any(marker in event_type for marker in ("tmux", "session", "runtime"))


def _no_progress_alerts(
    events: list[ZfEvent],
    *,
    threshold_minutes: int = 30,
) -> list[dict[str, Any]]:
    if not events:
        return [{
            "event_id": "",
            "type": "automation.no_progress",
            "task_id": "",
            "actor": "automation",
            "ts": "",
            "reason": "no events observed in monitor window",
            "threshold_minutes": threshold_minutes,
        }]
    parsed = [
        ts for ts in (_parse_ts(event.ts) for event in events)
        if ts is not None
    ]
    if not parsed:
        return []
    latest = max(parsed)
    age_minutes = int((datetime.now(timezone.utc) - latest).total_seconds() // 60)
    if age_minutes < threshold_minutes:
        return []
    return [{
        "event_id": "",
        "type": "automation.no_progress",
        "task_id": "",
        "actor": "automation",
        "ts": latest.isoformat(),
        "reason": f"no runtime events for {age_minutes} minutes",
        "threshold_minutes": threshold_minutes,
        "age_minutes": age_minutes,
    }]


def _event_ref(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    # ZF-E2E-MINI-P3 (2026-07-11): carry a content-based problem fingerprint
    # (registry dedupe_key_fields via abnormal_event_projection) so downstream
    # attention items fold repeats of the same problem. The per-event
    # automation:alerts:evt-<id> fingerprint made every repeat "new" — one
    # frozen budget produced 13 inbox rows.
    from zf.runtime.problem_taxonomy import abnormal_event_projection

    task_id = event.task_id or str(payload.get("task_id") or "")
    projection = abnormal_event_projection(event)
    problem_fingerprint = (
        str(projection.get("fingerprint") or "") if projection else ""
    ) or f"{event.type}:{task_id or event.actor or ''}"
    return redact_obj({
        "event_id": event.id,
        "type": event.type,
        "task_id": task_id,
        "actor": event.actor or "",
        "ts": event.ts,
        "reason": str(payload.get("reason") or payload.get("summary") or ""),
        "problem_fingerprint": problem_fingerprint,
    })


def _report_preview_refs(
    *,
    task_ids: list[str] | None = None,
    event_refs: list[dict[str, Any]] | None = None,
    proposal_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    clean_task_ids = _dedupe_strings(task_ids or [])[:50]
    if clean_task_ids:
        refs["task_refs"] = [
            {"kind": "task", "task_id": task_id, "name": task_id}
            for task_id in clean_task_ids
        ]
    clean_events = _compact_ref_list(list(event_refs or []), limit=50)
    if clean_events:
        refs["event_refs"] = clean_events
    clean_proposals = _compact_ref_list(list(proposal_refs or []), limit=20)
    if clean_proposals:
        refs["preview_refs"] = clean_proposals
    return redact_obj(refs)


def _dedupe_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _cost_by_role(state_dir: Path, *, last_days: int | None) -> dict[str, Any]:
    return {
        role: _cost_summary(summary)
        for role, summary in CostTracker(state_dir / "cost.jsonl").per_role_totals(
            last_days=last_days,
        ).items()
    }


def _cost_summary(summary: CostSummary) -> dict[str, Any]:
    return {
        "input_tokens": summary.input_tokens,
        "output_tokens": summary.output_tokens,
        "usd": round(summary.total_usd, 6),
        "entries": summary.entries,
    }


def _cost_trend(state_dir: Path, *, days: int) -> dict[str, Any]:
    daily = CostTracker(state_dir / "cost.jsonl").daily_totals()
    items = [
        {
            "date": date,
            "input_tokens": int(summary.get("input_tokens") or 0),
            "output_tokens": int(summary.get("output_tokens") or 0),
            "usd": round(float(summary.get("total_usd") or 0.0), 6),
            "entries": int(summary.get("entries") or 0),
        }
        for date, summary in sorted(daily.items())[-days:]
    ]
    return {
        "days": items,
        "total_usd": round(sum(item["usd"] for item in items), 6),
        "entries": sum(item["entries"] for item in items),
    }


def _role_session_meta(state_dir: Path) -> dict[str, dict[str, Any]]:
    path = state_dir / "role_sessions.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    meta = data.get("instance_meta", {}) or {}
    if not isinstance(meta, dict):
        return {}
    return {
        str(instance_id): dict(values) if isinstance(values, dict) else {}
        for instance_id, values in meta.items()
    }


def _worker_health(state_dir: Path, events: list[ZfEvent]) -> dict[str, Any]:
    states: dict[str, str] = {}
    for event in events:
        if event.type != "worker.state.changed" or not event.actor:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        to_state = str(payload.get("to") or "")
        if to_state:
            states[str(event.actor)] = to_state
    meta = _role_session_meta(state_dir)
    workers = []
    for instance_id, values in sorted(meta.items()):
        heartbeat = values.get("last_heartbeat_payload")
        heartbeat_payload = heartbeat if isinstance(heartbeat, dict) else {}
        workers.append(redact_obj({
            "instance_id": instance_id,
            "backend": str(values.get("backend") or ""),
            "state": states.get(instance_id, str(heartbeat_payload.get("state") or "unknown")),
            "last_heartbeat_at": str(values.get("last_heartbeat_at") or ""),
            "current_task_id": str(heartbeat_payload.get("current_task_id") or ""),
            "context_used_ratio": heartbeat_payload.get("context_used_ratio"),
        }))
    context_warnings = [
        _event_ref(event) for event in events
        if event.type == "worker.context.warning"
    ][-20:]
    context_criticals = [
        _event_ref(event) for event in events
        if event.type == "worker.context.critical"
    ][-20:]
    stuck_events = [
        _event_ref(event) for event in events
        if event.type in {"worker.stuck", "worker.stuck.recovery_failed"}
    ][-20:]
    return {
        "workers": workers[-50:],
        "state_counts": dict(sorted(Counter(states.values()).items())),
        "context_warnings": context_warnings,
        "context_criticals": context_criticals,
        "stuck_events": stuck_events,
        "attention_count": len(context_warnings) + len(context_criticals) + len(stuck_events),
    }


def _channel_attention(events: list[ZfEvent]) -> dict[str, Any]:
    replies: dict[str, dict[str, Any]] = {}
    workflows: dict[str, dict[str, Any]] = {}
    delivery_failures: list[dict[str, Any]] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type.startswith("channel.agent.reply."):
            request_id = str(payload.get("request_id") or "")
            if request_id:
                row = replies.setdefault(request_id, {
                    "request_id": request_id,
                    "status": "unknown",
                    "channel_id": str(payload.get("channel_id") or ""),
                    "target_member_id": str(payload.get("target_member_id") or ""),
                    "message_id": str(payload.get("message_id") or ""),
                    "event_id": event.id,
                    "reason": "",
                })
                row["event_id"] = event.id
                row["status"] = event.type.rsplit(".", 1)[-1]
                row["reason"] = str(payload.get("reason") or row.get("reason") or "")
        elif event.type == "channel.message.failed":
            delivery_failures.append(_event_ref(event))
        elif event.type == "workflow.invoke.requested":
            request_key = event.id
            workflows[request_key] = {
                "request_event_id": event.id,
                "status": "requested",
                "task_id": str(payload.get("task_id") or event.task_id or ""),
                "pattern_id": str(payload.get("pattern_id") or ""),
                "channel_id": str(payload.get("channel_id") or ""),
                "reason": str(payload.get("reason") or ""),
            }
        elif event.type in {"workflow.invoke.accepted", "workflow.invoke.rejected"}:
            source_event_id = str(payload.get("source_event_id") or "")
            if not source_event_id:
                continue
            row = workflows.setdefault(source_event_id, {
                "request_event_id": source_event_id,
                "task_id": str(payload.get("task_id") or event.task_id or ""),
                "pattern_id": str(payload.get("pattern_id") or ""),
                "channel_id": str(payload.get("channel_id") or ""),
                "reason": "",
            })
            row["status"] = event.type.rsplit(".", 1)[-1]
            row["event_id"] = event.id
            row["reason"] = str(payload.get("reason") or row.get("reason") or "")
    failed_replies = [
        redact_obj(row) for row in replies.values()
        if row.get("status") == "failed"
    ][-20:]
    pending_replies = [
        redact_obj(row) for row in replies.values()
        if row.get("status") in {"requested", "started"}
    ][-20:]
    rejected_workflows = [
        redact_obj(row) for row in workflows.values()
        if row.get("status") == "rejected"
    ][-20:]
    pending_workflows = [
        redact_obj(row) for row in workflows.values()
        if row.get("status") == "requested"
    ][-20:]
    return {
        "failed_replies": failed_replies,
        "pending_replies": pending_replies,
        "rejected_workflows": rejected_workflows,
        "pending_workflows": pending_workflows,
        "delivery_failures": delivery_failures[-20:],
        "summary": {
            "failed_replies": len(failed_replies),
            "pending_replies": len(pending_replies),
            "rejected_workflows": len(rejected_workflows),
            "pending_workflows": len(pending_workflows),
            "delivery_failures": len(delivery_failures),
        },
    }


def _pending_proposals(events: list[ZfEvent], *, project_id: str) -> list[dict[str, Any]]:
    proposals = _proposal_index(events, project_id=project_id)
    return [
        redact_obj(row)
        for row in proposals.values()
        if row.get("status") == "created"
    ][-20:]


def _proposal_status_summary(events: list[ZfEvent], *, project_id: str) -> dict[str, Any]:
    proposals = _proposal_index(events, project_id=project_id)
    counts = Counter(str(row.get("status") or "unknown") for row in proposals.values())
    return {
        "counts": dict(sorted(counts.items())),
        "recent": [redact_obj(row) for row in list(proposals.values())[-20:]],
    }


def _proposal_index(events: list[ZfEvent], *, project_id: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for event in events:
        if not event.type.startswith("automation.proposal."):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("project_id") or "") not in {"", project_id}:
            continue
        proposal_id = str(payload.get("proposal_id") or event.id)
        row = rows.setdefault(proposal_id, {
            "proposal_id": proposal_id,
            "automation_id": str(payload.get("automation_id") or ""),
            "summary": str(payload.get("summary") or payload.get("reason") or ""),
            "action": str(payload.get("action") or ""),
            "event_id": event.id,
        })
        row["status"] = event.type.rsplit(".", 1)[-1]
        row["event_id"] = event.id
        if payload.get("summary") or payload.get("reason"):
            row["summary"] = str(payload.get("summary") or payload.get("reason") or "")
        if payload.get("action"):
            row["action"] = str(payload.get("action") or "")
    return rows


def _failure_taxonomy(events: list[ZfEvent]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    unclassified: Counter[str] = Counter()
    refs = []
    for event in events:
        category = classify(event)
        if category is not None:
            counts[str(category.value)] += 1
            refs.append(_event_ref(event))
        elif _is_failure_event(event):
            unclassified[event.type] += 1
            refs.append(_event_ref(event))
    return {
        "counts": dict(sorted(counts.items())),
        "unclassified": dict(sorted(unclassified.items())),
        "recent": refs[-50:],
    }


def _backlog_drift(events: list[ZfEvent], status_counts: dict[str, int]) -> dict[str, Any]:
    created = [event for event in events if event.type == "task.created"]
    requeued = [event for event in events if event.type == "task.requeued"]
    deferred = [
        event for event in events
        if event.type in {"task.deferred", "task.superseded", "task.cancelled"}
    ]
    return {
        "created_events": len(created),
        "requeued_events": len(requeued),
        "deferred_events": len(deferred),
        "current_backlog": status_counts.get("backlog", 0),
        "current_blocked": status_counts.get("blocked", 0),
    }


_SCORECARD_RELIABILITY_TYPES = frozenset({
    "dispatch.silent_stall", "runtime.safe_halted", "circuit.tripped",
    "worker.stuck", "task.rework.capped", "orchestrator.tick.failed",
    "task.orphaned", "remediation.cascade",
})
_SCORECARD_GOVERNANCE_TYPES = frozenset({
    "workflow.inline_override", "scope.violation", "task.baseline_diverged",
    "provider.permission.snapshot.drift",
})


def _scorecard_insights(
    scorecard: dict[str, Any],
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    """Render the cross-cutting scorecard into severity-tagged insights.

    Same-source: every insight points back at the events that produced its
    count via ``source_events`` (no re-judgement).
    """
    insights: list[dict[str, Any]] = []
    reliability = scorecard.get("reliability", {})
    governance = scorecard.get("governance", {})

    incidents = int(reliability.get("incidents_total", 0))
    if incidents:
        critical = int(reliability.get("critical_total", 0))
        refs = [
            event.id for event in events
            if event.type in _SCORECARD_RELIABILITY_TYPES
        ]
        insights.append(_insight(
            "weekly-reliability-incidents",
            severity="critical" if critical else "warn",
            category="runtime",
            title="Harness reliability incidents",
            summary=(
                f"{incidents} self-reliability incident(s) this week "
                f"({critical} critical: safe-halt / circuit)."
            ),
            metric=incidents,
            source_events=refs[-50:],
            suggested_action=(
                "Inspect safe-halt / circuit events before the next unattended run."
                if critical else
                "Review stuck / stall / rework-cap signals for a recurring cause."
            ),
        ))

    violations = int(governance.get("violations_total", 0))
    if violations:
        refs = [
            event.id for event in events
            if event.type in _SCORECARD_GOVERNANCE_TYPES
        ]
        insights.append(_insight(
            "weekly-governance-violations",
            severity="warn",
            category="runtime",
            title="Governance / drift signals",
            summary=(
                f"{violations} governance signal(s): inline override / scope "
                "violation / baseline or permission drift."
            ),
            metric=violations,
            source_events=refs[-50:],
            suggested_action="Confirm overrides were intentional; the gate was bypassed.",
        ))

    return insights


def _delivery_metrics(tasks: list[Task]) -> dict[str, Any]:
    done = [task for task in tasks if task.status == "done"]
    cancelled = [task for task in tasks if task.status == "cancelled"]
    started_or_terminal = [
        task for task in tasks
        if task.status in {"done", "cancelled", "in_progress", "review", "verify", "testing", "blocked"}
    ]
    durations = [
        hours for hours in (_task_duration_hours(task) for task in done)
        if hours is not None
    ]
    denominator = len(done) + len(cancelled)
    return {
        "done": len(done),
        "cancelled": len(cancelled),
        "started_or_terminal": len(started_or_terminal),
        "terminal_success_rate": (
            round(len(done) / denominator, 4)
            if denominator else None
        ),
        "avg_done_cycle_hours": round(sum(durations) / len(durations), 2) if durations else None,
        "cycle_p50_hours": percentile(durations, 50),
        "cycle_p90_hours": percentile(durations, 90),
    }


def _task_duration_hours(task: Task) -> float | None:
    start = _parse_ts(task.started_at or task.dispatched_at or task.created_at)
    end = _parse_ts(task.completed_at)
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds() / 3600


def _automation_title(automation_id: str) -> str:
    return {
        "daily-brief": "Daily Brief",
        "weekly-review": "Weekly Review",
        "project-monitor": "Project Monitor",
    }.get(automation_id, automation_id)
