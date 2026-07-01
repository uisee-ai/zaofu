"""Stage report artifact IO helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.core.state.atomic_io import atomic_write_text


STAGE_REPORT_DIR = "stage-reports"


def write_stage_report(state_dir: Path, report: dict[str, Any]) -> dict[str, str]:
    state_dir = Path(state_dir)
    stage = _safe_stage(str(report.get("stage") or "unknown"))
    artifact_dir = state_dir / "artifacts" / STAGE_REPORT_DIR
    json_path = artifact_dir / f"{stage}-report.json"
    md_path = artifact_dir / f"{stage}-report.md"
    latest_path = artifact_dir / "latest.json"
    atomic_write_text(
        json_path,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(md_path, render_stage_report_markdown(report))
    latest = {
        "schema_version": "stage-report-latest.v1",
        "stage": stage,
        "json_path": _relative_ref(json_path, state_dir),
        "md_path": _relative_ref(md_path, state_dir),
        "trigger_event_id": str(
            (report.get("trigger_event") or {}).get("id") or ""
        ),
    }
    atomic_write_text(
        latest_path,
        json.dumps(latest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "latest_path": str(latest_path),
    }


def read_latest_stage_report(state_dir: Path) -> dict[str, Any]:
    state_dir = Path(state_dir)
    latest = _read_json(state_dir / "artifacts" / STAGE_REPORT_DIR / "latest.json")
    report_path = state_dir / str(latest.get("json_path") or "")
    report = _read_json(report_path)
    return {
        "schema_version": "stage-report-api.v1",
        "latest": latest,
        "report": report,
    }


def render_stage_report_markdown(report: dict[str, Any]) -> str:
    stage = str(report.get("stage") or "")
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    trigger = (
        report.get("trigger_event")
        if isinstance(report.get("trigger_event"), dict) else {}
    )
    lines = [
        f"# {stage} report",
        "",
        f"- schema: `{report.get('schema_version', '')}`",
        f"- trigger: `{trigger.get('type', '')}` `{trigger.get('id', '')}`",
        f"- task count: {summary.get('task_count', 0)}",
        f"- event count: {summary.get('event_count', 0)}",
        f"- next action: `{summary.get('next_action', '')}`",
        "",
        "## Tasks",
    ]
    for task in report.get("tasks", []):
        if not isinstance(task, dict):
            continue
        lines.append(
            f"- `{task.get('id', '')}` {task.get('status', '')} "
            f"assigned={task.get('assigned_to', '')}"
        )
    lines.extend(["", "## Artifacts"])
    for ref in report.get("artifact_refs", []):
        if isinstance(ref, str):
            lines.append(f"- `{ref}`")
    lines.extend(["", "## Events"])
    for event in report.get("events", []):
        if not isinstance(event, dict):
            continue
        lines.append(
            f"- `{event.get('type', '')}` `{event.get('id', '')}` "
            f"task={event.get('task_id', '')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _safe_stage(stage: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in stage)


def _relative_ref(path: Path, state_dir: Path) -> str:
    try:
        return str(path.relative_to(state_dir))
    except ValueError:
        return str(path)


__all__ = [
    "STAGE_REPORT_DIR",
    "read_latest_stage_report",
    "render_stage_report_markdown",
    "write_stage_report",
]
