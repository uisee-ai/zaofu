"""Task-scoped Agent Live projection."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog


ACTIVE_STATES = {"running", "busy", "in_progress", "working"}
QUEUED_EVENTS = {"task.assigned", "task.dispatched", "worker.reply.requested"}
DONE_EVENTS = {
    "dev.build.done",
    "review.approved",
    "review.rejected",
    "test.passed",
    "test.failed",
    "judge.passed",
    "judge.failed",
    "worker.completed",
}


def project_agent_live(
    state_dir: Path,
    *,
    events: list | None = None,
) -> dict[str, Any]:
    state_dir = Path(state_dir)
    if events is None:
        events = EventLog(state_dir / "events.jsonl").read_days(7)
    by_task: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "task_id": "",
        "workers": {},
        "route_events": [],
    })
    for event in events:
        task_id = str(event.task_id or "")
        payload = event.payload if isinstance(event.payload, dict) else {}
        instance_id = str(
            payload.get("instance_id")
            or payload.get("role_instance")
            or event.actor
            or ""
        )
        if not task_id and not instance_id:
            continue
        task = by_task[task_id or "_project"]
        task["task_id"] = task_id
        if event.type in QUEUED_EVENTS or event.type in DONE_EVENTS or event.type.startswith("worker."):
            worker = task["workers"].setdefault(instance_id, {
                "instance_id": instance_id,
                "task_id": task_id,
                "status": "observed",
                "last_event": "",
                "last_event_id": "",
                "events": 0,
            })
            worker["events"] += 1
            worker["last_event"] = event.type
            worker["last_event_id"] = event.id
            if event.type in QUEUED_EVENTS:
                worker["status"] = "queued"
            if event.type in DONE_EVENTS:
                worker["status"] = "done"
            if event.type == "worker.state.changed":
                state = str(payload.get("state") or payload.get("status") or "")
                worker["status"] = state or worker["status"]
            if event.type == "worker.heartbeat":
                worker["status"] = "running"
                worker["context_usage_ratio"] = payload.get("context_used_ratio")
            task["route_events"].append(event.id)
    usage_by_role = {
        role: {
            "input_tokens": summary.input_tokens,
            "output_tokens": summary.output_tokens,
            "usd": summary.total_usd,
            "entries": summary.entries,
        }
        for role, summary in CostTracker(state_dir / "cost.jsonl").per_role_totals().items()
    }
    tasks = []
    for row in by_task.values():
        workers = list(row["workers"].values())
        active = [w for w in workers if str(w.get("status")) in ACTIVE_STATES]
        queued = [w for w in workers if str(w.get("status")) == "queued"]
        tasks.append({
            "task_id": row["task_id"],
            "workers": workers,
            "active_workers": active,
            "queued_workers": queued,
            "route_events": row["route_events"][-30:],
        })
    return {
        "schema_version": "agent_live.v1",
        "tasks": tasks,
        "usage_by_role": usage_by_role,
    }

