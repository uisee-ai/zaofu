"""Per-task progress/evidence projections from runtime truth."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task

_COMPLETED_EVENTS = {
    "dev.build.done",
    "static_gate.passed",
    "lane.stage.completed",
    "verify.child.completed",
    "review.approved",
    "verify.passed",
    "test.passed",
    "judge.passed",
    "discriminator.passed",
    "task.done",
    "task.done.accepted",
}
_BLOCKER_EVENTS = {
    "dev.blocked",
    "review.rejected",
    "verify.failed",
    "test.failed",
    "judge.failed",
    "discriminator.failed",
    "task.rework.requested",
    "task.rework.capped",
    "dispatch.blocked",
}
_PROGRESS_EVENTS = {
    "task.dispatched",
    "task.assigned",
    "worker.heartbeat",
    "worker.progress",
    "task.ref.updated",
}


def render_projected_progress_doc(
    state_dir: Path,
    task: Task,
    *,
    generated_at: str,
) -> str:
    events = _task_events(state_dir, task.id)
    completed = [event for event in events if event.type in _COMPLETED_EVENTS]
    in_progress = [event for event in events if event.type in _PROGRESS_EVENTS]
    blockers = [event for event in events if event.type in _BLOCKER_EVENTS]
    lines = [
        f"# Progress for {task.id}",
        "",
        f"> generated_at: {generated_at}",
        "> projection only, not runtime truth",
        "",
        "## Current State",
        "",
        f"- status_hint: `{task.status}`",
        f"- assigned_to: `{task.assigned_to or ''}`",
        f"- active_dispatch_id: `{task.active_dispatch_id or ''}`",
        "",
        "## Completed",
        "",
    ]
    if completed:
        lines.extend(_event_bullets(completed[-8:]))
    else:
        lines.append("_No projected progress yet._")
    lines.extend(["", "## In Progress", ""])
    if in_progress:
        lines.extend(_event_bullets(in_progress[-8:]))
    else:
        lines.append("_No projected progress yet._")
    lines.extend(["", "## Blockers", ""])
    if task.blocked_reason:
        lines.append(f"- task.blocked_reason: {task.blocked_reason}")
    if blockers:
        lines.extend(_event_bullets(blockers[-8:]))
    if not task.blocked_reason and not blockers:
        lines.append("_No blocker projected._")
    lines.extend(["", "## Next Step", ""])
    lines.append(_next_step(task, completed=completed, blockers=blockers))
    return "\n".join(lines).rstrip() + "\n"


def render_projected_evidence_doc(
    state_dir: Path,
    task: Task,
    *,
    generated_at: str,
) -> str:
    events = _task_events(state_dir, task.id)
    refs = _task_ref(state_dir, task.id)
    evidence = asdict(task.evidence) if task.evidence else {}
    payload: dict[str, Any] = {
        "task_id": task.id,
        "status": task.status,
        "task_ref": refs,
        "task_evidence": evidence,
        "events": [_event_summary(event) for event in events if _is_evidence_event(event)],
    }
    lines = [
        f"# Evidence for {task.id}",
        "",
        f"> generated_at: {generated_at}",
        "> projection only, not runtime truth",
        "",
    ]
    if payload["events"] or payload["task_ref"] or payload["task_evidence"]:
        lines.append("```json")
        lines.append(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
    else:
        lines.append("_No evidence projected yet._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _task_events(state_dir: Path, task_id: str) -> list[ZfEvent]:
    try:
        events = EventLog(state_dir / "events.jsonl").read_all()
    except Exception:
        return []
    out: list[ZfEvent] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        payload_task = str(payload.get("task_id") or "")
        if event.task_id == task_id or payload_task == task_id:
            out.append(event)
    return out


def _task_ref(state_dir: Path, task_id: str) -> dict[str, Any]:
    path = state_dir / "refs" / "task-index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    value = data.get(task_id)
    return dict(value) if isinstance(value, dict) else {}


def _event_bullets(events: list[ZfEvent]) -> list[str]:
    return [
        f"- `{event.type}` `{event.id}`: {_event_text(event)}"
        for event in events
    ]


def _event_text(event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    for key in ("summary", "message", "reason", "outcome_reason"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value[:240]
    commands = payload.get("commands_run")
    if isinstance(commands, list) and commands:
        first = commands[0] if isinstance(commands[0], dict) else {}
        command = str(first.get("command") or "").strip()
        exit_code = first.get("exit_code", "")
        if command:
            return f"{command} exit_code={exit_code}"
    checks = payload.get("checks")
    if isinstance(checks, list) and checks:
        first = checks[0] if isinstance(checks[0], dict) else {}
        command = str(first.get("command") or "").strip()
        status = str(first.get("status") or "").strip()
        if command or status:
            return f"{command} {status}".strip()
    return ""


def _event_summary(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return {
        "event_id": event.id,
        "type": event.type,
        "actor": event.actor,
        "summary": _event_text(event),
        "source_commit": str(payload.get("source_commit") or ""),
        "source_branch": str(payload.get("source_branch") or ""),
        "commands_run": payload.get("commands_run", []),
        "checks": payload.get("checks", []),
        "verification_tiers": payload.get("verification_tiers", []),
        "artifact_refs": payload.get("artifact_refs", []),
        "evidence_refs": payload.get("evidence_refs", []),
    }


def _is_evidence_event(event: ZfEvent) -> bool:
    return (
        event.type in _COMPLETED_EVENTS
        or event.type in _BLOCKER_EVENTS
        or event.type == "task.ref.updated"
    )


def _next_step(
    task: Task,
    *,
    completed: list[ZfEvent],
    blockers: list[ZfEvent],
) -> str:
    if task.status == "done":
        return "_Task is done; inspect evidence.md for terminal proof._"
    if blockers:
        latest = blockers[-1]
        return f"_Resolve `{latest.type}` `{latest.id}` before continuing._"
    if completed:
        return "_Continue from the latest completed gate/event and wait for the next role._"
    return "_Read task.md and source.md, then continue from latest events/evidence._"
