"""Feishu-friendly Automation insight report renderer."""

from __future__ import annotations

import json
from typing import Any, Iterable


def render_automation_markdown(
    projection: dict[str, Any],
    *,
    automation_ids: Iterable[str] | None = None,
) -> str:
    selected = {str(item) for item in automation_ids or []}
    project_name = _text(projection.get("project_name") or projection.get("project_id") or "")
    project_id = _text(projection.get("project_id") or "")
    generated_at = _text(projection.get("generated_at") or "")
    items = [
        item for item in projection.get("items") or projection.get("automations") or []
        if isinstance(item, dict)
        and (not selected or _automation_id(item) in selected)
    ]
    insights = _all_insights(items)
    daily = _output_for(items, "daily-brief")
    weekly = _output_for(items, "weekly-review")
    monitor = _output_for(items, "project-monitor")
    lines = [
        "# ZaoFu Project Overview",
        "",
        f"**Project**: {_md_inline(project_name or project_id)}",
        f"**Project ID**: {_md_inline(project_id)}",
        f"**Generated At**: {_md_inline(generated_at)}",
        "",
    ]
    _append_project_status(lines, insights, items, daily)
    _append_action_required(lines, insights)
    _append_delivery_health(lines, weekly)
    _append_runtime_health(lines, monitor, items)
    _append_open_in_zaofu(lines)
    return "\n".join(lines).rstrip() + "\n"


def _append_project_status(
    lines: list[str],
    insights: list[dict[str, Any]],
    items: list[dict[str, Any]],
    daily: dict[str, Any],
) -> None:
    lines.extend([
        "## Project Status",
        "",
        f"- Status: {_overall_status(insights)}",
        f"- Main conclusion: {_main_conclusion(insights)}",
        f"- Coverage: {_coverage(items)}",
        (
            "- Today: "
            f"active={_active_count(daily)}, "
            f"done={_count(daily.get('done_tasks'))}, "
            f"blocked={_count(daily.get('blocked_tasks'))}, "
            f"failures={_count(daily.get('failure_events') or daily.get('failed_events'))}"
        ),
        f"- Board: {_board_counts_summary(daily.get('board_counts'))}",
        "",
    ])


def _append_action_required(lines: list[str], insights: list[dict[str, Any]]) -> None:
    lines.extend(["## Action Required", ""])
    actions = [
        item for item in insights
        if _severity_rank(item.get("severity")) <= _severity_rank("info")
        and (
            _text(item.get("suggested_action"))
            or str(item.get("severity") or "").lower() in {"critical", "warn"}
        )
    ]
    if not actions:
        lines.extend(["- No immediate action required from automation signals.", ""])
        return
    for insight in actions[:5]:
        action = _text(insight.get("suggested_action")) or _text(insight.get("summary"))
        if not action:
            action = _default_next_action(insight)
        lines.append(
            f"- [{_md_inline(_text(insight.get('severity')).upper())}] "
            f"{_md_inline(insight.get('title'))}: {_md_inline(action)}"
        )
    lines.append("")


def _append_delivery_health(lines: list[str], output: dict[str, Any]) -> None:
    delivery = output.get("delivery_metrics") if isinstance(output.get("delivery_metrics"), dict) else {}
    comparison = (
        output.get("delivery_metrics_14d")
        if isinstance(output.get("delivery_metrics_14d"), dict) else {}
    )
    taxonomy = output.get("failure_taxonomy") if isinstance(output.get("failure_taxonomy"), dict) else {}
    drift = output.get("backlog_drift") if isinstance(output.get("backlog_drift"), dict) else {}
    cost = output.get("cost_trend") if isinstance(output.get("cost_trend"), dict) else {}
    terminal_rate = delivery.get("terminal_success_rate")
    terminal_note = (
        _percent(terminal_rate)
        if terminal_rate is not None else
        "n/a (no terminal sample)"
    )
    lines.extend([
        "## Delivery Health",
        "",
        f"- Done / cancelled: {_value(delivery.get('done'))} / {_value(delivery.get('cancelled'))}",
        f"- Terminal success rate: {terminal_note}",
        f"- 14d success rate: {_percent(comparison.get('terminal_success_rate'))}",
        f"- Rework signals: {_count(output.get('rework_event_refs') or output.get('rework_events'))}",
        f"- Top failure class: {_top_failure(taxonomy)}",
        f"- Backlog drift: {_backlog_drift(drift)}",
        f"- Cost trend: {_cost_summary(cost)}",
        "",
    ])


def _append_runtime_health(
    lines: list[str],
    output: dict[str, Any],
    items: list[dict[str, Any]],
) -> None:
    worker_health = output.get("worker_health") if isinstance(output.get("worker_health"), dict) else {}
    sync = _sync_health_summary(items)
    lines.extend([
        "## Runtime Health",
        "",
        f"- Runtime alerts: {_count(output.get('alerts'))}",
        f"- Progress alerts: {_count(output.get('progress_alerts'))}",
        f"- Channel alerts: {_count(output.get('channel_alerts'))}",
        f"- Worker attention: {_value(worker_health.get('attention_count'))}",
        f"- Open proposals: {_count(output.get('open_proposals'))}",
        f"- Automation sync: completed={sync['completed']}, failed={sync['failed']}, skipped={sync['skipped']}",
        "",
    ])


def _append_open_in_zaofu(lines: list[str]) -> None:
    lines.extend([
        "## Open In ZaoFu",
        "",
        "- Use ZaoFu Web for Kanban, Events, Agent View, trace, and session drilldown.",
        "- Feishu shows the overview only; detailed diagnosis stays in ZaoFu.",
        "",
    ])


def _output_for(items: list[dict[str, Any]], automation_id: str) -> dict[str, Any]:
    for item in items:
        if _automation_id(item) == automation_id:
            return _first_dict(item.get("outputs"))
    return {}


def _sync_health_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"completed": 0, "failed": 0, "skipped": 0}
    for item in items:
        summary = item.get("run_counts_summary")
        if not isinstance(summary, dict):
            continue
        for key in totals:
            totals[key] += int(summary.get(key) or 0)
    return totals


def _default_next_action(insight: dict[str, Any]) -> str:
    category = _text(insight.get("category")).lower()
    if category == "delivery":
        return "Review delivery health in ZaoFu before expanding scope."
    if category in {"runtime", "agents", "channels", "progress"}:
        return "Open ZaoFu runtime views for diagnosis."
    if category == "proposals":
        return "Review the proposal through the controlled action path."
    return "Open ZaoFu for details."


def _automation_id(item: dict[str, Any]) -> str:
    return _text(item.get("automation_id") or "")


def _all_insights(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    insights: list[dict[str, Any]] = []
    for item in items:
        output = _first_dict(item.get("outputs"))
        for insight in output.get("insights") or []:
            if isinstance(insight, dict):
                insights.append(insight)
    return sorted(
        insights,
        key=lambda item: (_severity_rank(item.get("severity")), _text(item.get("title"))),
    )


def _overall_status(insights: list[dict[str, Any]]) -> str:
    if any(_text(item.get("severity")).lower() == "critical" for item in insights):
        return "critical - operator attention required"
    if any(_text(item.get("severity")).lower() == "warn" for item in insights):
        return "attention - review warnings before expanding scope"
    if any(_text(item.get("severity")).lower() == "info" for item in insights):
        return "informational - no critical automation alerts"
    return "clear - no automation concerns detected"


def _coverage(items: list[dict[str, Any]]) -> str:
    if not items:
        return "no automation sections selected"
    parts = []
    for item in items:
        title = _text(item.get("title") or _automation_id(item))
        window = _text(item.get("window") or _first_dict(item.get("outputs")).get("window"))
        parts.append(f"{title} ({window})" if window else title)
    return _md_inline("; ".join(parts))


def _main_conclusion(insights: list[dict[str, Any]]) -> str:
    if not insights:
        return "No automation insight was generated for this report window."
    first = insights[0]
    title = _text(first.get("title") or first.get("id"))
    summary = _text(first.get("summary"))
    return _md_inline(f"{title}: {summary}" if summary else title)


def _active_count(output: dict[str, Any]) -> int:
    active = _string_list(output.get("active_tasks"))
    if active:
        return len(active)
    counts = output.get("task_counts") if isinstance(output.get("task_counts"), dict) else {}
    active_statuses = {
        "backlog",
        "blocked",
        "in_progress",
        "review",
        "testing",
        "judge",
    }
    return sum(int(counts.get(status) or 0) for status in active_statuses)


def _board_counts_summary(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "no board projection"
    labels = ("Todo", "In Progress", "Verify", "Blocked", "Done")
    return _md_inline(", ".join(
        f"{label}={int(value.get(label) or 0)}" for label in labels
    ))


def _top_failure(taxonomy: dict[str, Any]) -> str:
    counts = taxonomy.get("counts") if isinstance(taxonomy.get("counts"), dict) else {}
    if not counts:
        return "none"
    key, value = max(
        ((str(key), int(value or 0)) for key, value in counts.items()),
        key=lambda item: item[1],
    )
    return _md_inline(f"{key} ({value})")


def _backlog_drift(drift: dict[str, Any]) -> str:
    if not drift:
        return "no drift data"
    return _md_inline(
        "created={created}, requeued={requeued}, deferred={deferred}, backlog={backlog}, blocked={blocked}".format(
            created=int(drift.get("created_events") or 0),
            requeued=int(drift.get("requeued_events") or 0),
            deferred=int(drift.get("deferred_events") or 0),
            backlog=int(drift.get("current_backlog") or 0),
            blocked=int(drift.get("current_blocked") or 0),
        ),
    )


def _cost_summary(cost: dict[str, Any]) -> str:
    if not cost:
        return "no cost data"
    return _md_inline(
        f"${float(cost.get('total_usd') or 0.0):.6f}, "
        f"{int(cost.get('entries') or 0)} entries",
    )


def _severity_rank(value: object) -> int:
    return {
        "critical": 0,
        "error": 0,
        "warn": 1,
        "warning": 1,
        "info": 2,
        "ok": 3,
    }.get(_text(value).lower(), 4)


def _count(raw: object) -> int:
    if raw is None:
        return 0
    if isinstance(raw, dict):
        return len(raw)
    if isinstance(raw, (list, tuple, set)):
        return len(raw)
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _value(raw: object) -> str:
    if raw is None or raw == "":
        return "0"
    return _md_inline(raw)


def _percent(raw: object) -> str:
    if raw is None or raw == "":
        return "n/a"
    try:
        return f"{float(raw) * 100:.1f}%"
    except (TypeError, ValueError):
        return _md_inline(raw)


def _string_list(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, (list, tuple, set)):
        return [_text(item) for item in raw if _text(item)]
    return [_text(raw)] if _text(raw) else []


def _first_dict(raw: object) -> dict[str, Any]:
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                return item
    return {}


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
