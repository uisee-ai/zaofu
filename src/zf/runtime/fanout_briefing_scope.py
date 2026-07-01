"""Fanout scoped context helpers for reducer/synthesis briefings."""

from __future__ import annotations

from typing import Any


def build_fanout_scope_summary(
    manifest: dict[str, Any],
    reports: list[dict[str, Any]] | None = None,
    *,
    current_status: Any | None = None,
) -> dict[str, Any]:
    fanout_id = str(manifest.get("fanout_id") or "")
    stale_reason = _current_status_value(current_status, "stale_reason")
    superseded_by = _current_status_value(current_status, "superseded_by")
    current_value = _current_status_value(current_status, "current")
    if current_value is False:
        return {
            "fanout_id": fanout_id,
            "stage_id": str(manifest.get("stage_id") or ""),
            "current_instance": False,
            "stale_filtered": True,
            "stale_reason": stale_reason or "fanout_instance_not_current",
            "superseded_by": superseded_by,
            "total_children": 0,
            "children": [],
            "with_output": [],
            "without_output": [],
        }
    report_by_child = {
        str(report.get("child_id") or ""): report
        for report in (reports or [])
        if isinstance(report, dict)
    }
    children: list[dict[str, str]] = []
    with_output: list[dict[str, str]] = []
    without_output: list[dict[str, str]] = []
    for child in manifest.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        child_id = str(child.get("child_id") or "")
        report = report_by_child.get(child_id, {})
        row = {
            "child_id": child_id,
            "role_instance": str(
                child.get("role_instance")
                or report.get("role_instance")
                or ""
            ),
            "status": str(child.get("status") or report.get("status") or ""),
            "report_path": str(
                child.get("report_path")
                or report.get("report_path")
                or ""
            ),
        }
        children.append(row)
        has_report = bool(row["report_path"]) or bool(report.get("report"))
        if has_report:
            with_output.append(row)
        else:
            without_output.append(row)
    return {
        "fanout_id": fanout_id,
        "stage_id": str(manifest.get("stage_id") or ""),
        "current_instance": current_value,
        "stale_filtered": False,
        "stale_reason": "",
        "superseded_by": "",
        "total_children": len(children),
        "children": children,
        "with_output": with_output,
        "without_output": without_output,
    }


def render_fanout_scope_briefing_lines(
    manifest: dict[str, Any],
    reports: list[dict[str, Any]] | None = None,
    *,
    max_children: int = 20,
    current_status: Any | None = None,
) -> list[str]:
    summary = build_fanout_scope_summary(
        manifest,
        reports,
        current_status=current_status,
    )
    if summary.get("stale_filtered"):
        lines = [
            "## Fanout Scope Summary",
            "",
            f"- fanout_id: `{summary['fanout_id']}`",
            f"- stage_id: `{summary['stage_id']}`",
            "- current_instance: false",
            f"- stale_reason: `{summary['stale_reason']}`",
        ]
        if summary.get("superseded_by"):
            lines.append(f"- superseded_by: `{summary['superseded_by']}`")
        lines.extend([
            "- total_children: 0",
            "",
            "This fanout instance is stale. Do not synthesize or approve from its "
            "child outputs; use the current fanout instance instead.",
            "",
        ])
        return lines
    children = summary["children"]
    if not children:
        return []
    render_limit = max(0, int(max_children or 0))
    displayed_children = children[:render_limit] if render_limit else children
    omitted_children = len(children) - len(displayed_children)
    lines = [
        "## Fanout Scope Summary",
        "",
        f"- fanout_id: `{summary['fanout_id']}`",
        f"- stage_id: `{summary['stage_id']}`",
        f"- total_children: {summary['total_children']}",
        f"- with_output: {_child_ids(summary['with_output'], max_items=render_limit)}",
        f"- without_output: {_child_ids(summary['without_output'], max_items=render_limit)}",
    ]
    if omitted_children > 0:
        lines.append(
            f"- context_budget_fallback: showing {len(displayed_children)}/"
            f"{len(children)} child rows; use artifact refs for omitted children"
        )
    lines.extend(["", "Child status table:"])
    for child in displayed_children:
        report_ref = child["report_path"] or "missing"
        lines.append(
            f"- `{child['child_id']}` role=`{child['role_instance']}` "
            f"status=`{child['status']}` report=`{report_ref}`"
        )
    if omitted_children > 0:
        lines.append(f"- ... {omitted_children} child rows omitted for context budget")
    lines.extend([
        "",
        "## Reducer Discipline",
        "",
        "Synthesize only from the child reports, manifest fields, event refs, and "
        "artifact refs listed in this briefing. Do not read or edit project source "
        "files to create new facts for missing children.",
        "Treat every child in `without_output` as explicit missing evidence; do not "
        "silently assume it passed.",
        "",
    ])
    return lines


def _current_status_value(current_status: Any | None, name: str) -> Any:
    if current_status is None:
        return None
    if isinstance(current_status, dict):
        return current_status.get(name)
    return getattr(current_status, name, None)


def _child_ids(children: list[dict[str, str]], *, max_items: int = 20) -> str:
    ids = [child["child_id"] for child in children if child.get("child_id")]
    if not ids:
        return "(none)"
    limit = max(0, int(max_items or 0))
    displayed = ids[:limit] if limit else ids
    suffix = f" (+{len(ids) - len(displayed)} omitted)" if len(ids) > len(displayed) else ""
    return ", ".join(f"`{child_id}`" for child_id in displayed) + suffix


__all__ = [
    "build_fanout_scope_summary",
    "render_fanout_scope_briefing_lines",
]
