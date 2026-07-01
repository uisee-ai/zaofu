"""Read-only project summary projection for the Kanban Agent."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from zf.core.config.schema import ZfConfig
from zf.core.events import EventLog, ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.autoresearch_invocation import autoresearch_invocation_projection
from zf.runtime.pane_probe import build_runtime_pane_probe
from zf.runtime.supervisor_inspection import read_supervisor_snapshot, supervisor_snapshot_ref


KANBAN_AGENT_SUMMARY_SCHEMA_VERSION = "kanban-agent.project-summary.v0"


def project_kanban_agent_summary(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    project_id: str = "",
    include_pane_probe: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a compact operator-facing summary from existing projections."""

    root = Path(project_root) if project_root is not None else Path(state_dir).parent
    current = now or datetime.now(timezone.utc)
    tasks = _read_tasks(Path(state_dir))
    events = _read_events(Path(state_dir), config=config)
    status_counts = Counter(str(task.status or "unknown") for task in tasks)
    simplified_counts = Counter(_simplified_status(str(task.status or "")) for task in tasks)
    active = [
        _task_summary(task)
        for task in tasks
        if _simplified_status(str(task.status or "")) in {"todo", "in_progress", "verify", "blocked"}
    ]
    recent_events = [
        _event_summary(event)
        for event in events[-80:]
        if _interesting_event_type(event.type)
    ][-20:]
    workflow_events = [
        event for event in events
        if event.type.startswith("workflow.") or event.type.startswith("delivery.")
    ]
    replan_loop = _replan_loop_summary(events)
    autoresearch = _safe_autoresearch_projection(events)
    supervisor = _safe_supervisor_snapshot(Path(state_dir))
    pane_probe = _safe_pane_probe(
        Path(state_dir),
        config=config,
        project_root=root,
        include_pane_probe=include_pane_probe,
        now=current,
    )
    role_sessions = _read_role_sessions(Path(state_dir))
    next_actions = _next_actions(
        tasks=tasks,
        supervisor=supervisor,
        autoresearch=autoresearch,
        pane_probe=pane_probe,
        workflow_events=workflow_events,
    )
    return redact_obj({
        "schema_version": KANBAN_AGENT_SUMMARY_SCHEMA_VERSION,
        "is_derived_projection": True,
        "generated_at": current.isoformat(),
        "project_id": project_id,
        "project_root": str(root),
        "state_dir": str(state_dir),
        "tasks": {
            "total": len(tasks),
            "by_status": dict(sorted(status_counts.items())),
            "by_board_column": {
                "todo": simplified_counts.get("todo", 0),
                "in_progress": simplified_counts.get("in_progress", 0),
                "verify": simplified_counts.get("verify", 0),
                "blocked": simplified_counts.get("blocked", 0),
                "done": simplified_counts.get("done", 0),
                "cancelled": simplified_counts.get("cancelled", 0),
                "other": simplified_counts.get("other", 0),
            },
            "active": active[:30],
        },
        "workflow": {
            "recent_event_count": len(workflow_events[-100:]),
            "latest": [_event_summary(event) for event in workflow_events[-10:]],
        },
        "replan_loop": replan_loop,
        "runtime": {
            "role_sessions": role_sessions,
            "pane_probe": pane_probe,
        },
        "supervisor": {
            "snapshot_ref": supervisor_snapshot_ref(Path(state_dir)),
            "summary": _supervisor_summary(supervisor),
        },
        "autoresearch": autoresearch,
        "recent_events": recent_events,
        "next_actions": next_actions,
    })


def _replan_loop_summary(events: list[ZfEvent]) -> dict[str, Any]:
    replan_types = {
        "plan.insight.discovered",
        "research.probe.requested",
        "research.probe.completed",
        "reflection.recorded",
        "replan.proposal.created",
        "replan.contract_eval.requested",
        "replan.contract_eval.completed",
        "replan.adoption.prepared",
        "replan.adoption.blocked",
        "replan.adoption.stale_rejected",
        "product_delivery.task_map.adopted",
        "replan.owner_decision.approved",
        "replan.owner_decision.deferred",
        "replan.owner_decision.rejected",
    }
    rows = [event for event in events if event.type in replan_types]
    latest = [_replan_event_summary(event) for event in rows[-12:]]
    pending_owner = [
        item for item in latest
        if item["event_type"] in {"replan.proposal.created", "replan.contract_eval.completed"}
        and item["decision"] in {"", "revise", "escalate", "adopt"}
    ]
    status = "idle"
    if latest:
        last_type = latest[-1]["event_type"]
        if last_type == "product_delivery.task_map.adopted":
            status = "adopted"
        elif last_type == "replan.owner_decision.rejected":
            status = "owner_rejected"
        elif last_type == "replan.owner_decision.deferred":
            status = "owner_deferred"
        elif last_type == "replan.owner_decision.approved":
            status = "owner_approved"
        elif last_type.startswith("replan.adoption."):
            status = "blocked"
        elif last_type in {"replan.proposal.created", "replan.contract_eval.completed"}:
            status = "owner_review"
        elif last_type.startswith("research.") or last_type == "plan.insight.discovered":
            status = "researching"
    return {
        "schema_version": "kanban-agent.replan-loop-summary.v0",
        "is_derived_projection": True,
        "status": status,
        "total_events": len(rows),
        "pending_owner_review": len(pending_owner),
        "latest": latest,
    }


def _replan_event_summary(event: ZfEvent) -> dict[str, str]:
    data = event.payload if isinstance(event.payload, dict) else {}
    nested_eval = data.get("eval") if isinstance(data.get("eval"), dict) else {}
    return {
        "id": event.id,
        "event_type": event.type,
        "task_id": event.task_id or str(data.get("task_id") or ""),
        "ts": event.ts,
        "insight_ref": str(data.get("source_insight_ref") or data.get("insight_ref") or ""),
        "proposal_ref": str(data.get("proposal_ref") or data.get("artifact_ref") or ""),
        "request_id": str(data.get("request_id") or ""),
        "candidate_task_map_ref": str(
            data.get("candidate_task_map_ref")
            or data.get("new_task_map_ref")
            or nested_eval.get("new_task_map_ref")
            or ""
        ),
        "decision": str(data.get("decision") or nested_eval.get("decision") or data.get("status") or ""),
        "reason": str(data.get("reason") or nested_eval.get("reason") or ""),
    }


def _read_tasks(state_dir: Path) -> list[Task]:
    try:
        return TaskStore(state_dir / "kanban.json").list_all_with_archive(last_days=14)
    except Exception:
        return []


def _read_events(state_dir: Path, *, config: ZfConfig | None) -> list[ZfEvent]:
    try:
        return EventLog(state_dir / "events.jsonl").read_all()
    except Exception:
        return []


def _read_role_sessions(state_dir: Path) -> dict[str, Any]:
    path = state_dir / "role_sessions.yaml"
    if not path.exists():
        return {"available": False, "roles": [], "summary": {}}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"available": False, "roles": [], "summary": {"read_error": True}}
    if not isinstance(data, dict):
        return {"available": False, "roles": [], "summary": {}}
    roles = []
    for key, value in sorted(data.items()):
        if not isinstance(value, dict):
            continue
        roles.append({
            "instance_id": str(key),
            "role": str(value.get("role") or value.get("role_name") or ""),
            "state": str(value.get("state") or ""),
            "current_task_id": str(value.get("current_task_id") or value.get("task_id") or ""),
            "last_heartbeat_at": str(value.get("last_heartbeat_at") or ""),
        })
    counts = Counter(role["state"] or "unknown" for role in roles)
    return {
        "available": True,
        "roles": roles,
        "summary": {"total": len(roles), "by_state": dict(sorted(counts.items()))},
    }


def _safe_autoresearch_projection(events: list[ZfEvent]) -> dict[str, Any]:
    try:
        return autoresearch_invocation_projection(events)
    except Exception as exc:
        return {
            "schema_version": "autoresearch.invocations.projection.v0",
            "is_derived_projection": True,
            "summary": {"total": 0, "pending": 0, "error": str(exc)},
            "recent": [],
        }


def _safe_supervisor_snapshot(state_dir: Path) -> dict[str, Any]:
    try:
        return read_supervisor_snapshot(state_dir)
    except Exception:
        return {}


def _safe_pane_probe(
    state_dir: Path,
    *,
    config: ZfConfig | None,
    project_root: Path,
    include_pane_probe: bool,
    now: datetime,
) -> dict[str, Any]:
    if not include_pane_probe:
        return {"enabled": False, "reason": "disabled_by_request"}
    try:
        return build_runtime_pane_probe(
            state_dir,
            config=config,
            project_root=project_root,
            now=now,
            capture_lines=20,
        )
    except Exception as exc:
        return {"enabled": False, "reason": "probe_failed", "error": str(exc)}


def _task_summary(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "board_column": _simplified_status(str(task.status or "")),
        "priority": task.priority,
        "assigned_to": task.assigned_to,
        "blocked_by": list(task.blocked_by or []),
    }


def _event_summary(event: ZfEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "task_id": event.task_id or "",
        "actor": event.actor,
        "ts": event.ts,
        "correlation_id": event.correlation_id or "",
    }


def _interesting_event_type(event_type: str) -> bool:
    prefixes = (
        "workflow.",
        "runtime.attention.",
        "autoresearch.",
        "supervisor.",
        "worker.",
        "task.",
        "kanban.agent.",
        "agent.session.",
    )
    return event_type.startswith(prefixes) or event_type in {
        "user.message",
        "web.action.completed",
        "runtime.action.failed",
    }


def _simplified_status(status: str) -> str:
    value = status.strip().lower()
    if value in {"todo", "backlog", "ready", "queued"}:
        return "todo"
    if value in {"in_progress", "doing", "running", "active", "assigned", "dispatched"}:
        return "in_progress"
    if value in {"review", "verify", "verifying", "test", "testing", "judge"}:
        return "verify"
    if value in {"blocked", "failed", "needs_human"}:
        return "blocked"
    if value in {"done", "completed", "shipped"}:
        return "done"
    if value in {"cancelled", "canceled"}:
        return "cancelled"
    return "other"


def _supervisor_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    items = snapshot.get("attention_items") if isinstance(snapshot, dict) else []
    rows = [item for item in items or [] if isinstance(item, dict)]
    severity = Counter(str(row.get("severity") or "unknown") for row in rows)
    open_rows = [
        row for row in rows
        if str(row.get("status") or "open") not in {"resolved", "closed", "done"}
    ]
    return {
        "available": bool(snapshot),
        "attention_total": len(rows),
        "attention_open": len(open_rows),
        "by_severity": dict(sorted(severity.items())),
        "top_open": [
            {
                "attention_id": str(row.get("attention_id") or ""),
                "severity": str(row.get("severity") or ""),
                "title": str(row.get("title") or ""),
                "task_id": str(row.get("task_id") or ""),
            }
            for row in open_rows[:5]
        ],
    }


def _next_actions(
    *,
    tasks: list[Task],
    supervisor: dict[str, Any],
    autoresearch: dict[str, Any],
    pane_probe: dict[str, Any],
    workflow_events: list[ZfEvent],
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    blocked = [task for task in tasks if _simplified_status(str(task.status or "")) == "blocked"]
    if blocked:
        actions.append({
            "kind": "inspect_blocked_tasks",
            "label": f"{len(blocked)} blocked task(s)",
            "suggested_action": "maintenance-prepare",
        })
    supervisor_summary = _supervisor_summary(supervisor)
    if supervisor_summary.get("attention_open"):
        actions.append({
            "kind": "supervisor_attention",
            "label": f"{supervisor_summary['attention_open']} open supervisor attention item(s)",
            "suggested_action": "maintenance-prepare",
        })
    pending_autoresearch = int((autoresearch.get("summary") or {}).get("pending") or 0)
    if pending_autoresearch:
        actions.append({
            "kind": "autoresearch_pending",
            "label": f"{pending_autoresearch} autoresearch request(s) pending",
            "suggested_action": "maintenance-prepare",
        })
    pane_summary = pane_probe.get("summary") if isinstance(pane_probe, dict) else {}
    mismatch = int((pane_summary or {}).get("mismatch") or 0)
    missing = int((pane_summary or {}).get("missing") or 0)
    if mismatch or missing:
        actions.append({
            "kind": "runtime_pane_probe_gap",
            "label": f"pane probe mismatch={mismatch} missing={missing}",
            "suggested_action": "runtime-restart",
        })
    if workflow_events:
        latest = workflow_events[-1]
        actions.append({
            "kind": "latest_workflow_event",
            "label": latest.type,
            "suggested_action": "",
        })
    return actions[:8]
