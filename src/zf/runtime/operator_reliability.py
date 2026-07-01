"""Read-only operator reliability projections for long-horizon agents."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj


COCKPIT_SIGNAL_TYPES = {
    "worker.stuck",
    "worker.stuck.recovered",
    "worker.probe.silent",
    "worker.context.warning",
    "worker.context.critical",
    "worker.drift.detected",
}
RECOVERY_EVENT_TYPES = {
    "recovery.run.started",
    "recovery.step.started",
    "recovery.step.completed",
    "recovery.step.failed",
    "recovery.run.completed",
}
OBSERVED_RECOVERY_TYPES = {
    "worker.checkpointed",
    "runtime.maintenance.entered",
    "runtime.maintenance.exited",
    "dispatch.paused",
    "dispatch.resumed",
}
RECOVERY_TRIGGER_TYPES = {
    "worker.stuck",
    "worker.probe.silent",
    "worker.context.warning",
    "worker.context.critical",
    "worker.drift.detected",
}
MUTATION_EVENT_TYPES = {
    "web.action.requested",
    "web.action.completed",
    "web.action.failed",
    "runtime.action.accepted",
    "runtime.action.completed",
    "runtime.action.failed",
    "runtime.action.rejected",
    "task.created",
    "task.updated",
    "task.status_changed",
    "task.contract.update",
    "task.evidence_linked",
    "assignment.intent.proposed",
    "workflow.invoke.requested",
    "workflow.invoke.accepted",
    "workflow.invoke.rejected",
    "channel.created",
    "channel.archived",
    "channel.member.added",
    "channel.member.removed",
    "channel.message.posted",
    "workdir.writer_synced",
    "workdir.dependency_apply.failed",
    "workdir.retired",
    "workdir.retire_failed",
    "reader.write_violation",
    "task.baseline_synced",
    "task.baseline_diverged",
}
WORKTREE_AUDIT_TYPES = {
    "workdir.writer_synced",
    "workdir.dependency_apply.failed",
    "workdir.retired",
    "workdir.retire_failed",
    "reader.write_violation",
    "worker.drift.detected",
    "task.baseline_synced",
    "task.baseline_diverged",
    "autoscale.scale_down.blocked",
}
SENSITIVE_KEY_PARTS = (
    "api_key",
    "access_key",
    "authorization",
    "bearer",
    "password",
    "private_key",
    "provider_raw",
    "raw_transcript",
    "secret",
    "token",
)


def project_agent_cockpit(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
    agents: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project worker health, stuck signals, and operator next actions.

    This projection is intentionally read-only. It folds kernel events and the
    existing Agent View rows into an operator cockpit without mutating runtime
    truth or inventing a second state model.
    """

    state_dir = Path(state_dir)
    events = events if events is not None else EventLog(state_dir / "events.jsonl").read_days(7)
    now = now or datetime.now(timezone.utc)
    indexed = list(enumerate(events, start=1))
    agents = agents or []
    agents_by_id = {
        str(agent.get("instance_id") or agent.get("session_id") or ""): agent
        for agent in agents
        if str(agent.get("instance_id") or agent.get("session_id") or "")
    }
    event_by_instance: dict[str, list[tuple[int, ZfEvent]]] = defaultdict(list)
    observed_instances: set[str] = set(agents_by_id)
    for seq, event in indexed:
        instance_id = _event_instance(event)
        if not instance_id:
            continue
        event_by_instance[instance_id].append((seq, event))
        if event.type in COCKPIT_SIGNAL_TYPES or event.type == "worker.heartbeat":
            observed_instances.add(instance_id)

    workers = [
        _project_worker_cockpit(
            instance_id,
            agents_by_id.get(instance_id, {}),
            event_by_instance.get(instance_id, []),
            now=now,
        )
        for instance_id in sorted(observed_instances)
    ]
    priority = {
        "stuck": 0,
        "silent": 1,
        "context_warn": 2,
        "drift": 3,
        "attention": 4,
        "fresh": 5,
        "unknown": 6,
    }
    workers.sort(key=lambda row: (
        priority.get(str(row.get("status")), 9),
        str(row.get("role") or ""),
        str(row.get("instance_id") or ""),
    ))
    summary = {
        "workers": len(workers),
        "stuck": sum(1 for row in workers if row.get("status") == "stuck"),
        "silent": sum(1 for row in workers if row.get("status") == "silent"),
        "context_warn": sum(1 for row in workers if row.get("status") == "context_warn"),
        "drift": sum(1 for row in workers if row.get("status") == "drift"),
        "attention": sum(1 for row in workers if row.get("status") == "attention"),
    }
    return {
        "schema_version": "agent-cockpit.v1",
        "generated_at": now.isoformat(),
        "state_dir": str(state_dir),
        "summary": summary,
        "workers": workers,
    }


def project_recovery_catalog(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project explicit recovery runs and observed recovery evidence."""

    state_dir = Path(state_dir)
    events = events if events is not None else EventLog(state_dir / "events.jsonl").read_all()
    now = now or datetime.now(timezone.utc)
    runs: dict[str, dict[str, Any]] = {}
    suggestions: list[dict[str, Any]] = []

    for seq, event in enumerate(events, start=1):
        if event.type in RECOVERY_EVENT_TYPES:
            _observe_explicit_recovery(runs, event, seq)
        elif event.type in OBSERVED_RECOVERY_TYPES:
            _observe_runtime_recovery(runs, event, seq)
        if event.type in RECOVERY_TRIGGER_TYPES:
            suggestions.append(_recovery_suggestion(event, seq))

    run_rows = [_finalize_run(row) for row in runs.values()]
    run_rows.sort(
        key=lambda row: (
            str(row.get("last_event_at") or row.get("started_at") or ""),
            str(row.get("run_id") or ""),
        ),
        reverse=True,
    )
    suggestions.sort(key=lambda row: int(row.get("seq") or 0), reverse=True)
    return {
        "schema_version": "recovery-catalog.v1",
        "generated_at": now.isoformat(),
        "state_dir": str(state_dir),
        "summary": {
            "runs": len(run_rows),
            "active": sum(1 for row in run_rows if row.get("status") in {"running", "started"}),
            "failed": sum(1 for row in run_rows if row.get("status") == "failed"),
            "suggestions": len(suggestions),
        },
        "runs": run_rows,
        "suggestions": suggestions[:30],
    }


def project_mutation_audit(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project controlled mutations and their evidence from EventLog."""

    state_dir = Path(state_dir)
    events = events if events is not None else EventLog(state_dir / "events.jsonl").read_all()
    now = now or datetime.now(timezone.utc)
    entries = [
        _mutation_entry(event, seq)
        for seq, event in enumerate(events, start=1)
        if _is_mutation_event(event)
    ]
    entries.sort(key=lambda row: int(row.get("seq") or 0), reverse=True)
    type_counts: dict[str, int] = {}
    for row in entries:
        mutation_type = str(row.get("mutation_type") or "unknown")
        type_counts[mutation_type] = type_counts.get(mutation_type, 0) + 1
    return {
        "schema_version": "mutation-audit.v1",
        "generated_at": now.isoformat(),
        "state_dir": str(state_dir),
        "summary": {
            "entries": len(entries),
            "types": type_counts,
            "failed": sum(1 for row in entries if row.get("status") in {"failed", "rejected"}),
        },
        "entries": entries[:200],
    }


def project_worktree_drift_audit(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project worktree drift, backup refs, stash refs, and recovery hints."""

    state_dir = Path(state_dir)
    events = events if events is not None else EventLog(state_dir / "events.jsonl").read_all()
    now = now or datetime.now(timezone.utc)
    entries = [
        _worktree_entry(event, seq)
        for seq, event in enumerate(events, start=1)
        if _is_worktree_audit_event(event)
    ]
    entries.sort(key=lambda row: int(row.get("seq") or 0), reverse=True)
    return {
        "schema_version": "worktree-drift-audit.v1",
        "generated_at": now.isoformat(),
        "state_dir": str(state_dir),
        "summary": {
            "entries": len(entries),
            "action_required": sum(1 for row in entries if row.get("action_required")),
            "backup_refs": sum(1 for row in entries if row.get("backup_ref")),
            "stashed_refs": sum(1 for row in entries if row.get("stashed_ref")),
        },
        "entries": entries[:200],
    }


def _project_worker_cockpit(
    instance_id: str,
    agent: dict[str, Any],
    indexed_events: list[tuple[int, ZfEvent]],
    *,
    now: datetime,
) -> dict[str, Any]:
    signal_events = [
        (seq, event) for seq, event in indexed_events if event.type in COCKPIT_SIGNAL_TYPES
    ]
    latest_event = indexed_events[-1][1] if indexed_events else None
    latest_heartbeat = next(
        (event for _, event in reversed(indexed_events) if event.type == "worker.heartbeat"),
        None,
    )
    latest_progress = next(
        (
            event for _, event in reversed(indexed_events)
            if event.type in {"worker.progress", "phase.progressed"}
        ),
        None,
    )
    task_id = str(
        agent.get("task_id")
        or agent.get("active_task")
        or _event_task(latest_event)
        or ""
    )
    heartbeat_at = str(
        _as_record(agent.get("freshness")).get("last_heartbeat_at")
        or agent.get("last_heartbeat")
        or (latest_heartbeat.ts if latest_heartbeat else "")
    )
    heartbeat_age = _first_number(
        _as_record(agent.get("freshness")).get("last_heartbeat_age_sec"),
        _age_seconds(heartbeat_at, now=now) if heartbeat_at else None,
    )
    progress_at = str(
        _as_record(agent.get("freshness")).get("last_progress_at")
        or (latest_progress.ts if latest_progress else "")
    )
    context_ratio = _first_number(
        agent.get("context_usage_ratio"),
        _as_record(agent.get("freshness")).get("context_usage_ratio"),
        _latest_context_ratio(indexed_events),
    )
    context_risk = _context_risk(context_ratio)
    signal_rows = [_signal_row(event, seq, now=now) for seq, event in signal_events[-12:]]
    status = _worker_status(
        agent=agent,
        indexed_events=indexed_events,
        context_ratio=context_ratio,
    )
    reasons = _worker_reasons(
        status=status,
        signal_rows=signal_rows,
        heartbeat_age=heartbeat_age,
        context_ratio=context_ratio,
        attention_state=str(agent.get("attention_state") or ""),
    )
    return {
        "instance_id": instance_id,
        "role": str(agent.get("parent_role") or agent.get("role_type") or _role_from_instance(instance_id)),
        "backend": str(agent.get("backend") or ""),
        "agent_kind": str(agent.get("agent_kind") or "worker"),
        "task_id": task_id,
        "status": status,
        "attention": str(agent.get("attention_state") or "idle"),
        "heartbeat_at": heartbeat_at,
        "heartbeat_age_sec": heartbeat_age,
        "last_progress_at": progress_at,
        "last_progress_age_sec": _age_seconds(progress_at, now=now) if progress_at else None,
        "last_event_type": str(agent.get("last_event_type") or (latest_event.type if latest_event else "")),
        "last_event_id": latest_event.id if latest_event else "",
        "context_usage_ratio": context_ratio,
        "context_risk": context_risk,
        "signal_count": len(signal_events),
        "signals": signal_rows,
        "reasons": reasons,
        "next_actions": _next_actions(status, context_risk),
    }


def _worker_status(
    *,
    agent: dict[str, Any],
    indexed_events: list[tuple[int, ZfEvent]],
    context_ratio: float | None,
) -> str:
    last_stuck = _last_seq(indexed_events, "worker.stuck")
    last_recovered = _last_seq(indexed_events, "worker.stuck.recovered")
    if last_stuck is not None and (last_recovered is None or last_stuck > last_recovered):
        return "stuck"
    last_silent = _last_seq(indexed_events, "worker.probe.silent")
    last_heartbeat = _last_seq(indexed_events, "worker.heartbeat")
    if last_silent is not None and (last_heartbeat is None or last_silent > last_heartbeat):
        return "silent"
    if (
        _last_seq(indexed_events, "worker.context.critical") is not None
        or _last_seq(indexed_events, "worker.context.warning") is not None
        or (context_ratio is not None and context_ratio >= 0.80)
    ):
        return "context_warn"
    if _last_seq(indexed_events, "worker.drift.detected") is not None:
        return "drift"
    if _needs_attention(str(agent.get("attention_state") or "")):
        return "attention"
    if indexed_events or agent:
        return "fresh"
    return "unknown"


def _worker_reasons(
    *,
    status: str,
    signal_rows: list[dict[str, Any]],
    heartbeat_age: float | None,
    context_ratio: float | None,
    attention_state: str,
) -> list[str]:
    reasons = [
        str(row.get("reason") or row.get("type") or "")
        for row in signal_rows
        if row.get("reason") or row.get("type")
    ][-5:]
    if status == "silent" and heartbeat_age is not None:
        reasons.append(f"heartbeat stale for {heartbeat_age:.0f}s")
    if status == "context_warn" and context_ratio is not None:
        reasons.append(f"context usage {context_ratio:.2f}")
    if status == "attention" and attention_state:
        reasons.append(attention_state)
    return [reason for reason in reasons if reason][:6]


def _next_actions(status: str, context_risk: str) -> list[str]:
    actions = ["view_trace"]
    if status in {"stuck", "silent"}:
        actions.extend(["open_recovery_catalog", "request_checkpoint", "operator_reply_or_respawn"])
    if status == "context_warn" or context_risk in {"warning", "critical"}:
        actions.extend(["build_resume_packet", "checkpoint_before_compaction"])
    if status == "drift":
        actions.extend(["review_worktree_drift", "sync_or_escalate"])
    return actions


def _observe_explicit_recovery(
    runs: dict[str, dict[str, Any]],
    event: ZfEvent,
    seq: int,
) -> None:
    payload = _payload(event)
    run_id = _recovery_run_id(event)
    run = _ensure_run(runs, run_id, event, seq, source="explicit")
    if event.type == "recovery.run.started":
        run["status"] = str(payload.get("status") or "running")
        run["started_at"] = event.ts
        run["trigger_event_id"] = str(payload.get("trigger_event_id") or event.causation_id or "")
    elif event.type == "recovery.run.completed":
        run["status"] = str(payload.get("status") or "completed")
        run["completed_at"] = event.ts
    elif event.type.startswith("recovery.step."):
        status = event.type.rsplit(".", 1)[-1]
        if status == "failed":
            run["status"] = "failed"
        _upsert_step(run, event, seq, status=status)
    _touch_run(run, event, seq)


def _observe_runtime_recovery(
    runs: dict[str, dict[str, Any]],
    event: ZfEvent,
    seq: int,
) -> None:
    payload = _payload(event)
    if event.type == "worker.checkpointed":
        run_id = "checkpoint:" + str(payload.get("checkpoint_id") or event.id)
        run = _ensure_run(runs, run_id, event, seq, source="observed")
        run["status"] = "completed"
        run["completed_at"] = event.ts
        _upsert_step(run, event, seq, status="completed", step_id="checkpoint")
        artifacts = [
            payload.get("resume_packet_path"),
            payload.get("dirty_diff_artifact"),
            payload.get("artifact_path"),
        ]
        run["evidence_refs"] = [str(value) for value in artifacts if value]
        _touch_run(run, event, seq)
        return

    run_id = "maintenance:" + str(
        payload.get("maintenance_id")
        or payload.get("run_id")
        or event.correlation_id
        or event.id
    )
    run = _ensure_run(runs, run_id, event, seq, source="observed")
    step_id = event.type.replace(".", "_")
    if event.type == "runtime.maintenance.entered":
        run["status"] = "running"
        run["started_at"] = event.ts
        _upsert_step(run, event, seq, status="completed", step_id=step_id)
    elif event.type in {"runtime.maintenance.exited", "dispatch.resumed"}:
        run["status"] = "completed"
        run["completed_at"] = event.ts
        _upsert_step(run, event, seq, status="completed", step_id=step_id)
    elif event.type == "dispatch.paused":
        _upsert_step(run, event, seq, status="completed", step_id=step_id)
    _touch_run(run, event, seq)


def _ensure_run(
    runs: dict[str, dict[str, Any]],
    run_id: str,
    event: ZfEvent,
    seq: int,
    *,
    source: str,
) -> dict[str, Any]:
    if run_id not in runs:
        runs[run_id] = {
            "run_id": run_id,
            "source": source,
            "status": "running",
            "task_id": _event_task(event),
            "instance_id": _event_instance(event),
            "started_at": event.ts,
            "last_event_at": event.ts,
            "last_seq": seq,
            "trigger_event_id": event.causation_id or event.id,
            "steps": [],
            "event_refs": [],
            "evidence_refs": [],
        }
    return runs[run_id]


def _upsert_step(
    run: dict[str, Any],
    event: ZfEvent,
    seq: int,
    *,
    status: str,
    step_id: str | None = None,
) -> None:
    payload = _payload(event)
    effective_step_id = str(
        step_id
        or payload.get("step_id")
        or payload.get("step")
        or payload.get("action")
        or event.type
    )
    steps = run.setdefault("steps", [])
    current = next((step for step in steps if step.get("step_id") == effective_step_id), None)
    if current is None:
        current = {
            "step_id": effective_step_id,
            "name": str(payload.get("name") or effective_step_id),
            "started_at": event.ts if status == "started" else "",
        }
        steps.append(current)
    current["status"] = status
    current["last_event_id"] = event.id
    current["last_seq"] = seq
    current["last_event_type"] = event.type
    current["last_event_at"] = event.ts
    if status == "completed":
        current["completed_at"] = event.ts
    if status == "failed":
        current["failed_at"] = event.ts
        current["reason"] = str(payload.get("reason") or payload.get("error") or "")


def _touch_run(run: dict[str, Any], event: ZfEvent, seq: int) -> None:
    run["last_event_at"] = event.ts
    run["last_seq"] = seq
    refs = run.setdefault("event_refs", [])
    refs.append(_event_ref(event, seq))
    run["event_refs"] = refs[-12:]
    if not run.get("task_id"):
        run["task_id"] = _event_task(event)
    if not run.get("instance_id"):
        run["instance_id"] = _event_instance(event)


def _finalize_run(run: dict[str, Any]) -> dict[str, Any]:
    steps = list(run.get("steps") or [])
    steps.sort(key=lambda step: int(step.get("last_seq") or 0))
    finalized = dict(run)
    finalized["steps"] = steps
    finalized["step_count"] = len(steps)
    finalized["failed_steps"] = sum(1 for step in steps if step.get("status") == "failed")
    return finalized


def _recovery_suggestion(event: ZfEvent, seq: int) -> dict[str, Any]:
    recommendation = {
        "worker.stuck": "open_recovery_catalog",
        "worker.probe.silent": "request_operator_reply_or_respawn",
        "worker.context.warning": "checkpoint_before_compaction",
        "worker.context.critical": "build_resume_packet_and_checkpoint",
        "worker.drift.detected": "review_worktree_drift",
    }.get(event.type, "inspect")
    payload = _payload(event)
    return {
        "seq": seq,
        "suggestion_type": event.type,
        "recommended_recovery": recommendation,
        "task_id": _event_task(event),
        "instance_id": _event_instance(event),
        "trigger_event_id": event.id,
        "reason": str(payload.get("reason") or payload.get("message") or ""),
        "ts": event.ts,
    }


def _is_mutation_event(event: ZfEvent) -> bool:
    if event.type in MUTATION_EVENT_TYPES:
        return True
    if event.type.startswith("web.action.") or event.type.startswith("runtime.action."):
        return True
    if event.type.startswith("workflow.invoke."):
        return True
    if event.type.startswith("workdir."):
        return True
    return False


def _mutation_entry(event: ZfEvent, seq: int) -> dict[str, Any]:
    payload = _payload(event)
    action = _payload_text(payload, "action") or _payload_text(payload, "canonical_action")
    mutation_type = _mutation_type(event, action)
    status = _mutation_status(event)
    target = _mutation_target(event, payload, action)
    evidence_refs = _extract_evidence_refs(payload)
    return {
        "seq": seq,
        "event_id": event.id,
        "event_type": event.type,
        "ts": event.ts,
        "actor": event.actor or "",
        "task_id": _event_task(event),
        "mutation_type": mutation_type,
        "action": action or "-",
        "target": target,
        "status": status,
        "controlled_path": _controlled_path(event),
        "causation_id": event.causation_id or "",
        "correlation_id": event.correlation_id or "",
        "evidence_refs": evidence_refs,
        "recovery_hint": _mutation_recovery_hint(event, mutation_type),
        "payload": _safe_payload(payload),
    }


def _mutation_type(event: ZfEvent, action: str) -> str:
    if event.type.startswith("workdir.") or event.type == "reader.write_violation":
        return "worktree"
    if event.type in {"task.baseline_synced", "task.baseline_diverged"}:
        return "worktree"
    if event.type == "assignment.intent.proposed" or action in {"assignment-propose", "assignment-intent"}:
        return "assignment_intent"
    if event.type.startswith("workflow.invoke.") or action == "workflow-invoke":
        return "workflow_invoke"
    if action == "create-task" or event.type == "task.created":
        return "task_create"
    if action == "update-task" or event.type in {"task.updated", "task.status_changed", "task.contract.update"}:
        return "task_update"
    if action.startswith("worker-"):
        return "worker_action"
    if action.startswith("channel-") or event.type.startswith("channel."):
        return "channel"
    if action:
        return action.replace("-", "_")
    return "unknown"


def _mutation_status(event: ZfEvent) -> str:
    payload = _payload(event)
    explicit = _payload_text(payload, "status")
    if explicit:
        return explicit
    if event.type.endswith(".failed"):
        return "failed"
    if event.type.endswith(".rejected"):
        return "rejected"
    if event.type.endswith(".completed") or event.type.endswith(".accepted"):
        return "completed"
    if event.type.endswith(".requested") or event.type.endswith(".proposed"):
        return "requested"
    return "observed"


def _mutation_target(event: ZfEvent, payload: dict[str, Any], action: str) -> str:
    for key in (
        "target",
        "task_id",
        "channel_id",
        "instance_id",
        "workdir",
        "project_path",
        "pattern_id",
    ):
        value = _payload_text(payload, key)
        if value:
            return value
    if event.task_id:
        return event.task_id
    if action:
        return action
    return "-"


def _controlled_path(event: ZfEvent) -> str:
    if event.type.startswith("web.action."):
        return "web_action"
    if event.type.startswith("runtime.action."):
        return "runtime_action"
    if event.type.startswith("workflow.invoke."):
        return "workflow_invoke"
    if event.type.startswith("workdir.") or event.type == "reader.write_violation":
        return "kernel_workdir"
    if event.type.startswith("task."):
        return "kernel_task"
    if event.type.startswith("channel."):
        return "channel"
    return "eventlog"


def _mutation_recovery_hint(event: ZfEvent, mutation_type: str) -> str:
    payload = _payload(event)
    if mutation_type == "worktree":
        return _worktree_hint(event, payload)
    if event.type.endswith(".failed") or event.type.endswith(".rejected"):
        return _payload_text(payload, "reason") or "inspect event and retry via controlled action"
    return ""


def _extract_evidence_refs(payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in (
        "event_id",
        "source_event_id",
        "workflow_invoke_event_id",
        "assignment_event_id",
        "evidence_refs",
        "artifact_refs",
        "backup_ref",
        "stashed_ref",
        "resume_packet_path",
    ):
        raw = payload.get(key)
        values = raw if isinstance(raw, list) else [raw] if raw else []
        refs.extend(str(value) for value in values if value)
    return refs


def _is_worktree_audit_event(event: ZfEvent) -> bool:
    if event.type in WORKTREE_AUDIT_TYPES:
        if event.type != "autoscale.scale_down.blocked":
            return True
        return _payload_text(_payload(event), "reason") == "dirty_workdir"
    return False


def _worktree_entry(event: ZfEvent, seq: int) -> dict[str, Any]:
    payload = _payload(event)
    backup_ref = _payload_text(payload, "backup_ref")
    stashed_ref = _payload_text(payload, "stashed_ref")
    status = _worktree_status(event, payload)
    action_required = status in {
        "dirty_retire_refused",
        "drift_detected",
        "baseline_diverged",
        "retire_failed",
    } or bool(stashed_ref)
    return {
        "seq": seq,
        "event_id": event.id,
        "event_type": event.type,
        "ts": event.ts,
        "actor": event.actor or "",
        "task_id": _event_task(event),
        "instance_id": _event_instance(event),
        "status": status,
        "workdir": _payload_text(payload, "workdir"),
        "project_path": _payload_text(payload, "project_path"),
        "backup_ref": backup_ref,
        "stashed_ref": stashed_ref,
        "dirty_files": _payload_list(payload, "dirty_files"),
        "source_ref": _payload_text(payload, "source_ref"),
        "before": _payload_text(payload, "before"),
        "after": _payload_text(payload, "after"),
        "action_required": action_required,
        "recovery_hint": _worktree_hint(event, payload),
        "payload": _safe_payload(payload),
    }


def _worktree_status(event: ZfEvent, payload: dict[str, Any]) -> str:
    if event.type == "workdir.writer_synced":
        if _payload_text(payload, "stashed_ref"):
            return "synced_with_stash"
        if _payload_text(payload, "backup_ref"):
            return "synced_with_backup"
        return "synced"
    if event.type == "workdir.retired":
        return "retired"
    if event.type == "workdir.retire_failed":
        if _payload_text(payload, "status") == "dirty":
            return "dirty_retire_refused"
        return "retire_failed"
    if event.type == "reader.write_violation":
        return "reader_dirty_reset"
    if event.type == "worker.drift.detected":
        return "drift_detected"
    if event.type == "task.baseline_diverged":
        return "baseline_diverged"
    if event.type == "task.baseline_synced":
        return "baseline_synced"
    if event.type == "autoscale.scale_down.blocked":
        return "dirty_retire_refused"
    return "observed"


def _worktree_hint(event: ZfEvent, payload: dict[str, Any]) -> str:
    backup_ref = _payload_text(payload, "backup_ref")
    stashed_ref = _payload_text(payload, "stashed_ref")
    project_path = _payload_text(payload, "project_path") or _payload_text(payload, "workdir")
    if stashed_ref:
        return f"inspect {stashed_ref}; cherry-pick or show files before cleanup"
    if backup_ref:
        return f"inspect {backup_ref}; reset/cherry-pick manually only after review"
    if event.type == "workdir.retire_failed" and _payload_text(payload, "status") == "dirty":
        return "inspect dirty worktree and checkpoint before retire"
    if event.type == "autoscale.scale_down.blocked":
        return "worker retirement blocked by dirty workdir; inspect and checkpoint"
    if event.type == "reader.write_violation":
        return "reader worktree was reset; inspect event status for discarded local writes"
    if event.type == "worker.drift.detected":
        return "compare worker branch against expected baseline before continuing"
    if event.type == "task.baseline_diverged":
        return "manual baseline reconciliation required before dispatch"
    if project_path:
        return f"inspect {project_path}"
    return ""


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return redact_obj(_drop_sensitive_keys(payload))


def _drop_sensitive_keys(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = _drop_sensitive_keys(item)
        return sanitized
    if isinstance(value, list):
        return [_drop_sensitive_keys(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_drop_sensitive_keys(item) for item in value)
    return value


def _signal_row(event: ZfEvent, seq: int, *, now: datetime) -> dict[str, Any]:
    payload = _payload(event)
    return {
        "seq": seq,
        "type": event.type,
        "event_id": event.id,
        "task_id": _event_task(event),
        "ts": event.ts,
        "age_sec": _age_seconds(event.ts, now=now),
        "reason": str(payload.get("reason") or payload.get("message") or event.type),
    }


def _event_ref(event: ZfEvent, seq: int) -> dict[str, Any]:
    return {
        "seq": seq,
        "event_id": event.id,
        "type": event.type,
        "task_id": _event_task(event),
        "actor": event.actor or "",
        "ts": event.ts,
    }


def _recovery_run_id(event: ZfEvent) -> str:
    payload = _payload(event)
    return str(
        payload.get("run_id")
        or payload.get("recovery_run_id")
        or event.correlation_id
        or event.id
    )


def _latest_context_ratio(indexed_events: list[tuple[int, ZfEvent]]) -> float | None:
    for _, event in reversed(indexed_events):
        value = _context_ratio(_payload(event))
        if value is not None:
            return value
    return None


def _context_ratio(payload: dict[str, Any]) -> float | None:
    for key in (
        "context_usage_ratio",
        "context_used_ratio",
        "usage_ratio",
        "context_ratio",
        "ratio",
    ):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _context_risk(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value >= 0.90:
        return "critical"
    if value >= 0.80:
        return "warning"
    return "normal"


def _last_seq(indexed_events: list[tuple[int, ZfEvent]], event_type: str) -> int | None:
    for seq, event in reversed(indexed_events):
        if event.type == event_type:
            return seq
    return None


def _payload(event: ZfEvent | None) -> dict[str, Any]:
    if event is None or not isinstance(event.payload, dict):
        return {}
    return event.payload


def _payload_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return str(value).strip() if value is not None else ""


def _payload_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if value:
        return [str(value)]
    return []


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _event_instance(event: ZfEvent | None) -> str:
    payload = _payload(event)
    return str(
        payload.get("instance_id")
        or payload.get("role_instance")
        or payload.get("worker_id")
        or payload.get("agent_id")
        or (event.actor if event is not None else "")
        or ""
    )


def _event_task(event: ZfEvent | None) -> str:
    payload = _payload(event)
    return str(
        (event.task_id if event is not None else "")
        or payload.get("task_id")
        or payload.get("current_task_id")
        or payload.get("active_task")
        or payload.get("parent_task_id")
        or ""
    )


def _role_from_instance(instance_id: str) -> str:
    return instance_id.split("-", 1)[0] if "-" in instance_id else instance_id


def _needs_attention(value: str) -> bool:
    return value not in {"", "idle", "working", "completed_verified"}


def _first_number(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _age_seconds(value: str, *, now: datetime) -> float | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed).total_seconds())
