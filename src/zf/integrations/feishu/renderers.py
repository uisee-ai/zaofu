"""Render ZaoFu projections into Feishu-friendly documents and table rows."""

from __future__ import annotations

import json
from typing import Any, Iterable

from zf.core.task.kanban_projection import kanban_column_projection
from zf.core.task.schema import Task
from zf.integrations.feishu.automation_renderer import render_automation_markdown


DEFAULT_KANBAN_FIELD_MAP: dict[str, str] = {
    "task_id": "Task ID",
    "title": "Title",
    "status": "Status",
    "board_column": "Board Column",
    "assigned_to": "Assigned To",
    "priority": "Priority",
    "blocked_by": "Blocked By",
    "blocked_reason": "Blocker",
    "created_at": "Created At",
    "started_at": "Started At",
    "completed_at": "Completed At",
    "project_id": "Project ID",
    "project_name": "Project",
    "synced_at": "Synced At",
}

DEFAULT_AUTOMATION_FIELD_MAP: dict[str, str] = {
    "row_key": "Row Key",
    "record_type": "Record Type",
    "date": "Date",
    "project_id": "Project ID",
    "project_name": "Project",
    "automation_id": "Automation",
    "window": "Window",
    "status": "Status",
    "severity": "Severity",
    "highlight": "Highlight",
    "highlight_rank": "Highlight Rank",
    "highlight_reason": "Highlight Reason",
    "category": "Category",
    "title": "Title",
    "summary": "Summary",
    "suggested_action": "Suggested Action",
    "metric": "Metric",
    "task_refs": "Task Refs",
    "event_refs": "Event Refs",
    "synced_at": "Synced At",
}


def build_kanban_records(
    tasks: Iterable[Task],
    *,
    project_id: str,
    project_name: str,
    synced_at: str,
    field_map: dict[str, str] | None = None,
    phases: dict[str, str | None] | None = None,
    ready_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    fields = {**DEFAULT_KANBAN_FIELD_MAP, **(field_map or {})}
    records: list[dict[str, Any]] = []
    for task in sorted(tasks, key=_task_sort_key):
        projection = kanban_column_projection(
            task,
            phase=(phases or {}).get(task.id),
            ready=task.id in (ready_ids or set()),
        )
        records.append({
            fields["task_id"]: _bitable_text(task.id),
            fields["title"]: _bitable_text(task.title),
            fields["status"]: _bitable_text(task.status),
            fields["board_column"]: _bitable_text(projection.label),
            fields["assigned_to"]: _bitable_text(task.assigned_to),
            fields["priority"]: _bitable_text(task.priority),
            fields["blocked_by"]: _bitable_text(", ".join(task.blocked_by)),
            fields["blocked_reason"]: _bitable_text(task.blocked_reason),
            fields["created_at"]: _bitable_text(task.created_at),
            fields["started_at"]: _bitable_text(task.started_at),
            fields["completed_at"]: _bitable_text(task.completed_at or task.cancelled_at),
            fields["project_id"]: _bitable_text(project_id),
            fields["project_name"]: _bitable_text(project_name),
            fields["synced_at"]: _bitable_text(synced_at),
        })
    return records


def build_automation_insight_records(
    projection: dict[str, Any],
    *,
    synced_at: str,
    field_map: dict[str, str] | None = None,
    automation_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    fields = {**DEFAULT_AUTOMATION_FIELD_MAP, **(field_map or {})}
    selected = {str(item) for item in automation_ids or []}
    project_id = _text(projection.get("project_id"))
    project_name = _text(projection.get("project_name") or project_id)
    date_key = _text(projection.get("generated_at"))[:10] or synced_at[:10]
    records: list[dict[str, Any]] = []
    for item in projection.get("items") or projection.get("automations") or []:
        if not isinstance(item, dict):
            continue
        automation_id = _text(item.get("automation_id"))
        if selected and automation_id not in selected:
            continue
        output = _first_dict(item.get("outputs"))
        window = _text(item.get("window") or output.get("window"))
        status = _text(item.get("status") or "idle")
        title = _text(item.get("title") or automation_id)
        records.append(_automation_record(
            fields,
            row_key=f"{date_key}:{project_id}:{automation_id}:summary",
            record_type="summary",
            date_key=date_key,
            project_id=project_id,
            project_name=project_name,
            automation_id=automation_id,
            window=window,
            status=status,
            severity=_summary_severity(item, output),
            category="summary",
            title=title,
            summary=_text(output.get("summary")),
            suggested_action="",
            metric="",
            task_refs=_summary_task_refs(output),
            event_refs=_summary_event_refs(item, output),
            synced_at=synced_at,
        ))
        for insight in _dicts(output.get("insights")):
            insight_id = _text(insight.get("id") or insight.get("title"))
            records.append(_automation_record(
                fields,
                row_key=f"{date_key}:{project_id}:{automation_id}:insight:{insight_id}",
                record_type="insight",
                date_key=date_key,
                project_id=project_id,
                project_name=project_name,
                automation_id=automation_id,
                window=window,
                status=status,
                severity=_text(insight.get("severity") or "info"),
                category=_text(insight.get("category") or "insight"),
                title=_text(insight.get("title") or insight_id),
                summary=_text(insight.get("summary")),
                suggested_action=_text(insight.get("suggested_action")),
                metric=_text(insight.get("metric")),
                task_refs=_join_values(insight.get("task_ids"), limit=8),
                event_refs=_join_values(insight.get("source_events"), limit=5),
                synced_at=synced_at,
            ))
    return records


def render_kanban_records_markdown(records: list[dict[str, Any]]) -> str:
    if not records:
        return "# ZaoFu Kanban Sync\n\nNo tasks to sync.\n"
    columns = list(records[0].keys())
    rows = [[_cell(record.get(column)) for column in columns] for record in records]
    return "# ZaoFu Kanban Sync\n\n" + _table(columns, rows) + "\n"


def render_automation_insight_records_markdown(records: list[dict[str, Any]]) -> str:
    if not records:
        return "# ZaoFu Automation Insights Sync\n\nNo insights to sync.\n"
    columns = list(records[0].keys())
    rows = [[_cell(record.get(column)) for column in columns] for record in records]
    return "# ZaoFu Automation Insights Sync\n\n" + _table(columns, rows) + "\n"


def build_stale_automation_insight_record(
    row_key: str,
    *,
    project_id: str,
    project_name: str,
    synced_at: str,
    field_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    fields = {**DEFAULT_AUTOMATION_FIELD_MAP, **(field_map or {})}
    parts = row_key.split(":")
    date_key = parts[0] if parts else synced_at[:10]
    automation_id = parts[2] if len(parts) > 2 else ""
    return {
        fields["row_key"]: _bitable_text(row_key),
        fields["record_type"]: "stale",
        fields["date"]: _bitable_text(date_key),
        fields["project_id"]: _bitable_text(project_id),
        fields["project_name"]: _bitable_text(project_name),
        fields["automation_id"]: _bitable_text(automation_id),
        fields["window"]: "",
        fields["status"]: "stale",
        fields["severity"]: "info",
        fields["highlight"]: "Normal",
        fields["highlight_rank"]: "99",
        fields["highlight_reason"]: "",
        fields["category"]: "stale",
        fields["title"]: "Stale Automation Insight",
        fields["summary"]: "No longer present in the current Automation projection.",
        fields["suggested_action"]: "",
        fields["metric"]: "",
        fields["task_refs"]: "",
        fields["event_refs"]: "",
        fields["synced_at"]: _bitable_text(synced_at),
    }


def kanban_task_id_field(field_map: dict[str, str] | None = None) -> str:
    return {**DEFAULT_KANBAN_FIELD_MAP, **(field_map or {})}["task_id"]


def kanban_board_column_field(field_map: dict[str, str] | None = None) -> str:
    return {**DEFAULT_KANBAN_FIELD_MAP, **(field_map or {})}["board_column"]


def automation_row_key_field(field_map: dict[str, str] | None = None) -> str:
    return {**DEFAULT_AUTOMATION_FIELD_MAP, **(field_map or {})}["row_key"]


def _automation_record(
    fields: dict[str, str],
    *,
    row_key: str,
    record_type: str,
    date_key: str,
    project_id: str,
    project_name: str,
    automation_id: str,
    window: str,
    status: str,
    severity: str,
    category: str,
    title: str,
    summary: str,
    suggested_action: str,
    metric: str,
    task_refs: str,
    event_refs: str,
    synced_at: str,
) -> dict[str, Any]:
    highlight, highlight_rank, highlight_reason = _highlight_fields(
        automation_id=automation_id,
        severity=severity,
        category=category,
        title=title,
        summary=summary,
        suggested_action=suggested_action,
        metric=metric,
    )
    return {
        fields["row_key"]: _bitable_text(row_key),
        fields["record_type"]: _bitable_text(record_type),
        fields["date"]: _bitable_text(date_key),
        fields["project_id"]: _bitable_text(project_id),
        fields["project_name"]: _bitable_text(project_name),
        fields["automation_id"]: _bitable_text(automation_id),
        fields["window"]: _bitable_text(window),
        fields["status"]: _bitable_text(status),
        fields["severity"]: _bitable_text(severity),
        fields["highlight"]: _bitable_text(highlight),
        fields["highlight_rank"]: _bitable_text(highlight_rank),
        fields["highlight_reason"]: _bitable_text(highlight_reason),
        fields["category"]: _bitable_text(category),
        fields["title"]: _bitable_text(title),
        fields["summary"]: _bitable_text(summary),
        fields["suggested_action"]: _bitable_text(suggested_action),
        fields["metric"]: _bitable_text(metric),
        fields["task_refs"]: _bitable_text(task_refs),
        fields["event_refs"]: _bitable_text(event_refs),
        fields["synced_at"]: _bitable_text(synced_at),
    }


def _summary_severity(item: dict[str, Any], output: dict[str, Any]) -> str:
    status = _text(item.get("status")).lower()
    if status == "failed":
        return "critical"
    if _text(output.get("type")).lower() == "alert":
        return "warn"
    return "info"


def _highlight_fields(
    *,
    automation_id: str,
    severity: str,
    category: str,
    title: str,
    summary: str,
    suggested_action: str,
    metric: str,
) -> tuple[str, str, str]:
    haystack = " ".join([
        automation_id,
        severity,
        category,
        title,
        summary,
        suggested_action,
        metric,
    ]).lower()
    severity_key = severity.lower()
    reason = suggested_action or summary or title
    if severity_key in {"critical", "error", "failed"}:
        return "P0 Action Required", "00", reason
    if _has_live_term(haystack, ("proposal", "decision", "approval")):
        return "Decision Needed", "10", reason
    runtime_signal = category.lower() == "runtime" or _has_live_term(
        haystack,
        ("runtime alert", "provider", "tool", "budget", "heartbeat", "runtime"),
    )
    if runtime_signal and (
        severity_key in {"warn", "warning"} or _metric_is_nonzero(metric)
    ):
        return "Runtime Alert", "30", reason
    if _has_live_term(haystack, ("blocked", "stuck", "context pressure")):
        return "Blocked", "20", reason
    channel_signal = _has_live_term(haystack, ("channel", "pending", "rejected"))
    if (
        category.lower() == "channels"
        and (severity_key in {"warn", "warning"} or _metric_is_nonzero(metric))
    ) or channel_signal:
        return "Channel Attention", "40", reason
    delivery_signal = _has_live_term(
        haystack,
        ("rework", "failure", "backlog drift", "success rate"),
    )
    if (
        category.lower() == "delivery"
        and (severity_key in {"warn", "warning"} or _metric_is_nonzero(metric))
    ) or delivery_signal:
        return "Delivery Risk", "50", reason
    if severity_key in {"warn", "warning"}:
        return "Watch", "60", reason
    return "Normal", "90", ""


def _has_live_term(haystack: str, terms: tuple[str, ...]) -> bool:
    return any(term in haystack and not _is_zero_term(haystack, term) for term in terms)


def _is_zero_term(haystack: str, term: str) -> bool:
    plural = term if term.endswith("s") else f"{term}s"
    patterns = (
        f"0 {term}",
        f"0 {plural}",
        f"0 recent {term}",
        f"0 recent {plural}",
        f"0 pending {term}",
        f"0 pending {plural}",
        f"no {term}",
        f"no {plural}",
        f"no pending {term}",
        f"no pending {plural}",
    )
    return any(pattern in haystack for pattern in patterns)


def _metric_is_nonzero(value: object) -> bool:
    text = _text(value).lower()
    return bool(text and text not in {"0", "0.0", "0%", "none", "n/a", "na"})


def _summary_task_refs(output: dict[str, Any]) -> str:
    refs: list[str] = []
    for key in ("active_tasks", "blocked_tasks", "done_tasks", "cancelled_tasks"):
        refs.extend(_string_values(output.get(key)))
    return _join_values(refs, limit=8)


def _summary_event_refs(item: dict[str, Any], output: dict[str, Any]) -> str:
    refs: list[str] = []
    for row in _dicts(item.get("source_events")):
        refs.append(_text(row.get("event_id")))
    for key in ("alerts", "progress_alerts", "channel_alerts", "failure_events"):
        for row in _dicts(output.get(key)):
            refs.append(_text(row.get("event_id")))
    refs.extend(_string_values(output.get("failed_events")))
    return _join_values(refs, limit=5)


def _first_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, list):
        return {}
    for item in value:
        if isinstance(item, dict):
            return item
    return {}


def _dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def _join_values(value: object, *, limit: int = 24) -> str:
    seen: list[str] = []
    for item in _string_values(value):
        if item and item not in seen:
            seen.append(item)
    suffix = ""
    if len(seen) > limit:
        suffix = f", +{len(seen) - limit} more"
    return ", ".join(seen[:limit]) + suffix


def _bitable_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    header = "| " + " | ".join(_md_inline(item) for item in headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = [
        "| " + " | ".join(_md_inline(cell) for cell in row) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def _task_sort_key(task: Task) -> tuple[int, int, str]:
    order = {
        "blocked": 0,
        "in_progress": 1,
        "review": 2,
        "testing": 3,
        "judge": 4,
        "backlog": 5,
        "done": 6,
        "cancelled": 7,
    }
    return (order.get(task.status, 50), int(task.priority or 0), task.id)


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = _text(value).replace("\n", " ").replace("\r", " ")
    return text if len(text) <= 160 else text[:157] + "..."


def _md_inline(value: object) -> str:
    return _cell(value).replace("|", "\\|")


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
