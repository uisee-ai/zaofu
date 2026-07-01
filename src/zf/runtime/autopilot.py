"""Deterministic Autopilot proposal runner.

Autopilot v1 is proposal-only: it scans existing runtime truth and emits
attention proposals. It never mutates task/session truth directly.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.config.schema import AutopilotConfig, ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


AUTOPILOT_PROPOSAL_EVENT = "autopilot.proposal.created"
_TERMINAL_TASK_STATUSES = {"done", "cancelled"}
_ACTIVE_STALE_STATUSES = {"in_progress", "review", "testing", "blocked"}
_ATTENTION_EVENT_TYPES = {
    "task.orphan_warning": "orphan_warning",
    "task.orphaned": "orphaned_task",
    "worker.stuck": "worker_stuck",
    "worker.context.critical": "context_critical",
    "circuit.tripped": "circuit_tripped",
    "ship.blocked": "ship_blocked",
}
_FAILED_EVENT_TYPES = {
    "test.failed",
    "judge.failed",
    "discriminator.failed",
    "gate.failed",
    "runtime.action.failed",
    "web.action.failed",
}
_REJECTED_EVENT_TYPES = {
    "review.rejected",
    "runtime.action.rejected",
}


@dataclass(frozen=True)
class AutopilotProposal:
    proposal_id: str
    dedupe_key: str
    kind: str
    severity: str
    title: str
    reason: str
    source: str = "autopilot"
    mode: str = "proposal_only"
    task_id: str = ""
    action_proposal: dict[str, Any] | None = None
    signal: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = 1
        data["status"] = "open"
        if self.action_proposal is None:
            data.pop("action_proposal", None)
        else:
            data["suggested_action"] = str(self.action_proposal.get("action") or "")
        return redact_obj(data)


@dataclass(frozen=True)
class AutopilotTickResult:
    enabled: bool
    mode: str
    dry_run: bool
    created: list[AutopilotProposal]
    skipped_duplicates: int = 0

    @property
    def created_count(self) -> int:
        return len(self.created)


def run_autopilot_tick(
    state_dir: Path,
    *,
    config: ZfConfig | None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> AutopilotTickResult:
    """Scan runtime state and emit proposal events when configured.

    The function is deterministic over kanban/events inputs plus ``now``.
    It writes only ``autopilot.proposal.created`` events unless ``dry_run``
    is true.
    """
    autopilot = _autopilot_config(config)
    if not autopilot.enabled:
        return AutopilotTickResult(
            enabled=False,
            mode=autopilot.mode,
            dry_run=dry_run,
            created=[],
        )
    if autopilot.mode != "proposal_only":
        raise ValueError(f"unsupported autopilot mode: {autopilot.mode}")

    state_dir = Path(state_dir)
    event_log = event_log_from_project(state_dir, config=config)
    events = event_log.read_all()
    tasks = TaskStore(state_dir / "kanban.json").list_all()
    now = _utc(now)
    existing = _existing_dedupe_keys(events)
    candidates = _collect_proposals(
        tasks=tasks,
        events=events,
        autopilot=autopilot,
        now=now,
    )
    new_proposals = [
        proposal for proposal in candidates
        if proposal.dedupe_key not in existing
    ]
    skipped = len(candidates) - len(new_proposals)

    if not dry_run and new_proposals:
        writer = EventWriter(event_log)
        for proposal in new_proposals:
            writer.emit(
                AUTOPILOT_PROPOSAL_EVENT,
                actor="autopilot",
                task_id=proposal.task_id or None,
                payload=proposal.payload(),
            )

    return AutopilotTickResult(
        enabled=True,
        mode=autopilot.mode,
        dry_run=dry_run,
        created=new_proposals,
        skipped_duplicates=skipped,
    )


def _autopilot_config(config: ZfConfig | None) -> AutopilotConfig:
    if config is None:
        return AutopilotConfig()
    return getattr(config, "autopilot", AutopilotConfig())


def _collect_proposals(
    *,
    tasks: list[Task],
    events: list[ZfEvent],
    autopilot: AutopilotConfig,
    now: datetime,
) -> list[AutopilotProposal]:
    proposals: list[AutopilotProposal] = []
    latest_by_task = _latest_task_events(events)

    for task in tasks:
        if task.status in _TERMINAL_TASK_STATUSES:
            continue
        if task.status == "blocked" or task.blocked_reason:
            proposals.append(_blocked_task_proposal(task))
            continue
        if task.status in _ACTIVE_STALE_STATUSES:
            latest = latest_by_task.get(task.id)
            last_seen = _task_last_seen(task, latest)
            if last_seen is not None:
                age_hours = _hours_between(last_seen, now)
                if age_hours >= autopilot.stale_after_hours:
                    proposals.append(_stale_task_proposal(task, latest, age_hours))

    for event in events:
        kind = _attention_kind(event)
        if kind is None:
            continue
        event_time = _parse_ts(event.ts)
        if event_time is not None:
            age_hours = _hours_between(event_time, now)
            if age_hours > autopilot.failed_event_window_hours:
                continue
        proposals.append(_event_attention_proposal(event, kind))

    proposals.sort(key=lambda proposal: (proposal.kind, proposal.task_id, proposal.dedupe_key))
    return proposals


def _blocked_task_proposal(task: Task) -> AutopilotProposal:
    reason = task.blocked_reason or "task status is blocked"
    dedupe_key = f"blocked_task:{task.id}:{_stable_part(reason)}"
    return AutopilotProposal(
        proposal_id=_proposal_id(dedupe_key),
        dedupe_key=dedupe_key,
        kind="blocked_task",
        severity="high",
        task_id=task.id,
        title=f"{task.id} is blocked",
        reason=reason,
        action_proposal=_update_task_action(
            task.id,
            status="blocked",
            blocked_reason=reason or "autopilot confirmed blocked task",
        ),
        signal={
            "task_id": task.id,
            "status": task.status,
            "blocked_reason": task.blocked_reason,
        },
    )


def _stale_task_proposal(
    task: Task,
    latest: ZfEvent | None,
    age_hours: float,
) -> AutopilotProposal:
    rounded_age = round(age_hours, 1)
    latest_id = latest.id if latest is not None else "task-created-at"
    dedupe_key = f"stale_task:{task.id}:{latest_id}"
    reason = f"no task-scoped event for {rounded_age:g} hours"
    return AutopilotProposal(
        proposal_id=_proposal_id(dedupe_key),
        dedupe_key=dedupe_key,
        kind="stale_task",
        severity="medium",
        task_id=task.id,
        title=f"{task.id} looks stale",
        reason=reason,
        action_proposal=_update_task_action(
            task.id,
            status="blocked",
            blocked_reason=f"autopilot stale scan: {reason}",
        ),
        signal={
            "task_id": task.id,
            "status": task.status,
            "latest_event_id": latest.id if latest is not None else "",
            "latest_event_type": latest.type if latest is not None else "",
            "age_hours": rounded_age,
        },
    )


def _event_attention_proposal(event: ZfEvent, kind: str) -> AutopilotProposal:
    task_id = event.task_id or _payload_str(event.payload, "task_id")
    event_type = event.type
    severity = "high" if kind in {"failed_event", "action_rejected"} else "medium"
    title_subject = task_id or _payload_str(event.payload, "role") or "project"
    dedupe_key = f"{kind}:{event.id}"
    reason = _event_reason(event)
    return AutopilotProposal(
        proposal_id=_proposal_id(dedupe_key),
        dedupe_key=dedupe_key,
        kind=kind,
        severity=severity,
        task_id=task_id,
        title=f"{title_subject} needs triage: {event_type}",
        reason=reason,
        action_proposal=(
            _update_task_action(
                task_id,
                status="blocked",
                blocked_reason=f"autopilot triage: {event_type}: {reason}",
            )
            if task_id else None
        ),
        signal={
            "event_id": event.id,
            "event_type": event_type,
            "event_ts": event.ts,
            "task_id": task_id,
            "actor": event.actor or "",
        },
    )


def _attention_kind(event: ZfEvent) -> str | None:
    if event.type in _ATTENTION_EVENT_TYPES:
        return _ATTENTION_EVENT_TYPES[event.type]
    if event.type in _FAILED_EVENT_TYPES or event.type.endswith(".failed"):
        return "failed_event"
    if event.type in _REJECTED_EVENT_TYPES or event.type.endswith(".rejected"):
        return "action_rejected" if event.type.startswith("runtime.action.") else "rejected_event"
    return None


def _update_task_action(
    task_id: str,
    *,
    status: str,
    blocked_reason: str,
) -> dict[str, Any]:
    return {
        "action": "update-task",
        "payload": {
            "task_id": task_id,
            "status": status,
            "blocked_reason": blocked_reason,
            "source": "autopilot-proposal",
        },
        "reason": blocked_reason,
        "confidence": "deterministic",
        "mutates_task_state": True,
    }


def _latest_task_events(events: list[ZfEvent]) -> dict[str, ZfEvent]:
    latest: dict[str, ZfEvent] = {}
    for event in events:
        if event.type.startswith("autopilot."):
            continue
        if event.task_id:
            latest[event.task_id] = event
    return latest


def _task_last_seen(task: Task, latest: ZfEvent | None) -> datetime | None:
    if latest is not None:
        parsed = _parse_ts(latest.ts)
        if parsed is not None:
            return parsed
    return _parse_ts(task.created_at)


def _existing_dedupe_keys(events: list[ZfEvent]) -> set[str]:
    keys: set[str] = set()
    for event in events:
        if event.type != AUTOPILOT_PROPOSAL_EVENT:
            continue
        key = str((event.payload or {}).get("dedupe_key") or "")
        if key:
            keys.add(key)
    return keys


def _proposal_id(dedupe_key: str) -> str:
    digest = hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()[:12]
    return f"ap-{digest}"


def _stable_part(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]


def _event_reason(event: ZfEvent) -> str:
    payload = event.payload or {}
    for key in ("reason", "error", "message", "status"):
        value = payload.get(key)
        if value:
            return str(value)
    return f"observed {event.type}"


def _payload_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    return str(value) if value is not None else ""


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _hours_between(start: datetime, end: datetime) -> float:
    return max(0.0, (end - _utc(start)).total_seconds() / 3600.0)
