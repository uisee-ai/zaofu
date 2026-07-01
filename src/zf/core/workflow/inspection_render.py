"""Rendering and artifact helpers for workflow inspection reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.core.state.atomic_io import atomic_write_text


def render_workflow_inspection_markdown(report: dict[str, Any]) -> str:
    """Render the report for operators. JSON is the machine contract."""

    project = report.get("project", {})
    summary = report.get("summary", {})
    lines = [
        "# Workflow Inspect",
        "",
        f"- Status: `{report.get('status', 'GO')}`",
        f"- Project: `{project.get('name', '')}`",
        f"- Root: `{project.get('root', '')}`",
        f"- State: `{project.get('state_dir', '')}`",
        f"- Roles: `{summary.get('roles', 0)}`",
        f"- Stages: `{summary.get('stages', 0)}`",
        f"- Graph: `{summary.get('graph_nodes', 0)} nodes / {summary.get('graph_edges', 0)} edges`",
        "",
        "## Diagnostics",
    ]
    diagnostics = list(report.get("diagnostics", []) or [])
    if not diagnostics:
        lines.append("- OK: 未发现 workflow preflight 阻断项。")
    else:
        for item in diagnostics:
            detail = _format_diag_detail(item)
            lines.append(
                f"- [{item.get('severity', 'INFO')}] `{item.get('kind', '')}`: "
                f"{item.get('message', '')}{detail}"
            )

    lines.extend(["", "## Stages"])
    stages = list(report.get("stages", []) or [])
    if stages:
        lines.append("| stage | trigger | topology | roles | success | failure |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for stage in stages:
            lines.append(
                "| "
                + " | ".join(
                    _pipe_cell(str(value))
                    for value in (
                        stage.get("id", ""),
                        stage.get("trigger", ""),
                        stage.get("topology", ""),
                        ", ".join(stage.get("roles", []) or []),
                        stage.get("success_event", ""),
                        stage.get("failure_event", ""),
                    )
                )
                + " |"
            )
    else:
        lines.append("- 未配置 `workflow.stages`。")

    lines.extend(["", "## Roles"])
    roles = list(report.get("roles", []) or [])
    if roles:
        lines.append("| role | backend | triggers | publishes | skills |")
        lines.append("| --- | --- | --- | --- | --- |")
        for role in roles:
            lines.append(
                "| "
                + " | ".join(
                    _pipe_cell(str(value))
                    for value in (
                        role.get("instance_id", "") or role.get("name", ""),
                        role.get("backend", ""),
                        ", ".join(role.get("triggers", []) or []),
                        ", ".join(role.get("publishes", []) or []),
                        ", ".join(role.get("skills", []) or []),
                    )
                )
                + " |"
            )
    else:
        lines.append("- 未配置 roles。")

    lanes = list(report.get("affinity_lanes", []) or [])
    if lanes:
        lines.extend(["", "## Affinity"])
        lines.append("| profile | key | lane | impl | review | verify |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for item in lanes:
            lines.append(
                "| "
                + " | ".join(
                    _pipe_cell(str(value))
                    for value in (
                        item.get("profile", ""),
                        item.get("affinity_key", ""),
                        item.get("id", ""),
                        item.get("impl", ""),
                        item.get("review", ""),
                        item.get("verify", ""),
                    )
                )
                + " |"
            )

    multi = list(summary.get("multi_consumer_triggers", []) or [])
    if multi:
        lines.extend(["", "## Multi-Consumer Triggers"])
        for item in multi:
            lines.append(
                f"- `{item.get('event', '')}` -> "
                f"{', '.join(item.get('consumers', []) or [])}"
            )
    return "\n".join(lines) + "\n"


def write_workflow_inspection_artifacts(
    report: dict[str, Any],
    *,
    state_dir: Path,
) -> dict[str, str]:
    artifact_dir = state_dir / "artifacts" / "workflow-inspect"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifact_dir / "inspect.json"
    md_path = artifact_dir / "inspect.md"
    atomic_write_text(
        json_path,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(md_path, render_workflow_inspection_markdown(report))
    return {
        "json": str(json_path),
        "md": str(md_path),
    }


def _format_diag_detail(item: dict[str, Any]) -> str:
    parts = []
    for key in ("role", "stage_id", "event", "field"):
        value = str(item.get(key, "") or "")
        if value:
            parts.append(f"{key}={value}")
    if not parts:
        return ""
    return " (" + ", ".join(parts) + ")"


def _pipe_cell(value: str) -> str:
    return value.replace("|", "\\|") or "-"
