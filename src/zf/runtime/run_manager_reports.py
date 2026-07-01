"""Run Manager retrospective and candidate artifact builders."""

from __future__ import annotations

from typing import Any


def build_retrospective_markdown(
    *,
    projection: dict[str, Any],
) -> str:
    summary = projection.get("summary") if isinstance(projection.get("summary"), dict) else {}
    monitor = projection.get("monitor") if isinstance(projection.get("monitor"), dict) else {}
    advisor = projection.get("advisor") if isinstance(projection.get("advisor"), dict) else {}
    lines = [
        "# Run Manager Retrospective",
        "",
        f"- goal_status: {summary.get('goal_status', 'unknown')}",
        f"- completion_status: {summary.get('completion_status', 'unknown')}",
        f"- monitor_state: {monitor.get('state', 'unknown')}",
        f"- pending_actions: {summary.get('pending_actions', 0)}",
        f"- blocked_actions: {summary.get('blocked_actions', 0)}",
        f"- advisor_recommendations: {(advisor.get('summary') or {}).get('recommendations', 0)}",
        "",
        "## Timeline",
    ]
    for item in (projection.get("timeline") or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- {item.get('event_type', '')}: {item.get('action') or item.get('transition') or item.get('decision') or item.get('status') or ''}"
        )
    lines.extend(["", "## Recommendations"])
    for item in advisor.get("recommendations") or []:
        if not isinstance(item, dict):
            continue
        lines.append(f"- {item.get('kind')}: {item.get('reason')} -> {item.get('recommended_route')}")
    return "\n".join(lines).rstrip() + "\n"


def build_regression_backlog_candidates(
    *,
    projection: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = []
    no_progress = projection.get("no_progress") if isinstance(projection.get("no_progress"), dict) else {}
    for item in no_progress.get("items") or []:
        if not isinstance(item, dict):
            continue
        candidates.append({
            "schema_version": "run-manager.backlog-candidate.v1",
            "status": "proposed",
            "kind": "regression_scenario",
            "title": "No-progress fingerprint regression",
            "fingerprint": str(item.get("fingerprint") or ""),
            "source_event_id": str(item.get("event_id") or ""),
            "body": _candidate_body(item),
        })
    return candidates


def _candidate_body(item: dict[str, Any]) -> str:
    return "\n".join([
        "> 状态: proposed",
        "",
        "# No-progress Fingerprint Regression",
        "",
        f"- fingerprint: `{item.get('fingerprint', '')}`",
        f"- source_event_id: `{item.get('event_id', '')}`",
        f"- count: `{item.get('count', '')}`",
        "",
        "## 验收",
        "",
        "1. reproduce event slice -> verify: no-progress projection trips for this fingerprint",
        "2. apply fix -> verify: expected downstream event appears before threshold",
    ]) + "\n"


__all__ = ["build_regression_backlog_candidates", "build_retrospective_markdown"]
