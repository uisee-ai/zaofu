"""Run-scoped, read-only Goal Dossier composition projection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.feature.store import FeatureStore
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.core.task.store import TERMINAL_STATES, TaskStore
from zf.runtime.attempt_handoff_reducer import reduce_attempt_handoffs
from zf.runtime.attempt_ledger import failure_fingerprint
from zf.runtime.goal_closure_projection import build_goal_closure_loop
from zf.runtime.operation_projection import project_task_operations
from zf.runtime.progress_projection import project_task_progress
from zf.runtime.run_archive import RunArchiveError, validate_run_id
from zf.runtime.run_manager import build_run_goal_projection
from zf.runtime.run_scope import events_for_run, resolve_run_id
from zf.runtime.terminal_events import is_successful_run_terminal
from zf.runtime.workflow_anchor import is_workflow_fanout_anchor_task


SCHEMA_VERSION = "goal-dossier.v1"
_FAILURE_EVENTS = frozenset({
    "candidate.failed",
    "candidate.quality.failed",
    "integration.failed",
    "judge.failed",
    "review.rejected",
    "run.delivery.blocked",
    "run.delivery.failed",
    "run.goal.blocked",
    "run.goal.completion.blocked",
    "run.goal.completion.rejected",
    "task.rework.capped",
    "test.failed",
    "verify.failed",
})
_FAILURE_SETTLEMENT_EVENTS = {
    "candidate.failed": frozenset({
        "candidate.integration.completed", "candidate.ready", "candidate.updated",
    }),
    "candidate.quality.failed": frozenset({
        "candidate.integration.completed", "candidate.ready", "candidate.updated",
    }),
    "integration.failed": frozenset({
        "candidate.integration.completed", "candidate.ready", "candidate.updated",
    }),
    "judge.failed": frozenset({"judge.passed"}),
    "review.rejected": frozenset({"review.approved"}),
    "run.delivery.blocked": frozenset({"run.delivery.completed", "ship.completed", "ship.done"}),
    "run.delivery.failed": frozenset({"run.delivery.completed", "ship.completed", "ship.done"}),
    "run.goal.blocked": frozenset({"run.goal.completed", "run.completed"}),
    "run.goal.completion.blocked": frozenset({"run.goal.completed", "run.completed"}),
    "run.goal.completion.rejected": frozenset({"run.goal.completed", "run.completed"}),
    "task.rework.capped": frozenset({"task.done.accepted", "task.attempt.succeeded"}),
    "test.failed": frozenset({"test.passed"}),
    "verify.failed": frozenset({"verify.passed"}),
}


class GoalDossierError(ValueError):
    """The requested run cannot be projected without guessing."""


def build_goal_dossier(
    state_dir: Path,
    run_id: str,
    *,
    events: list[ZfEvent] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compose one run from canonical stores and existing replay reducers."""

    state_dir = Path(state_dir)
    requested = str(run_id or "").strip()
    if not requested:
        raise GoalDossierError("run_id is required")
    all_events = (
        list(events)
        if events is not None
        else EventLog(state_dir / "events.jsonl").read_all()
    )
    canonical_run_id = resolve_run_id(all_events, requested)
    if not canonical_run_id:
        raise GoalDossierError(f"unknown run_id: {requested!r}")
    scoped_events = events_for_run(all_events, run_id=canonical_run_id)
    if not scoped_events:
        raise GoalDossierError(f"run has no readable events: {requested!r}")

    generated_at = (now or datetime.now(timezone.utc)).isoformat()
    goal_id = _goal_id(scoped_events)
    goal = build_run_goal_projection(all_events, run_id=canonical_run_id)
    discovered_task_ids = _task_ids(scoped_events)
    workflow_anchor_task_ids = _workflow_anchor_task_ids(
        state_dir, discovered_task_ids,
    )
    tasks = _task_rows(state_dir, discovered_task_ids)
    task_ids = [str(task.get("id") or "") for task in tasks]
    feature = _feature_row(state_dir, goal_id)
    progress = {
        task_id: project_task_progress(
            state_dir,
            task_id,
            events=scoped_events,
            now=now,
        )
        for task_id in task_ids
    }
    handoff = reduce_attempt_handoffs(
        scoped_events,
        workflow_run_id=canonical_run_id,
    )
    operations = _operations(state_dir, task_ids, scoped_events)
    closure = build_goal_closure_loop(
        {},
        events=list(enumerate(scoped_events, start=1)),
        feature_id=goal_id,
    )
    artifact_refs, digests = _source_refs(scoped_events)
    task_digest = _digest(tasks)
    feature_digest = _digest(feature) if feature else ""
    event_fingerprint = _digest([_event_source(event) for event in scoped_events])
    source_manifest = {
        "event_ids": [event.id for event in scoped_events if event.id],
        "artifact_refs": artifact_refs,
        "digests": digests,
        "store_generations": {
            "events": event_fingerprint,
            "tasks": task_digest,
            "feature": feature_digest,
        },
    }
    source_fingerprint = _digest({
        "run_id": canonical_run_id,
        "events": event_fingerprint,
        "tasks": task_digest,
        "feature": feature_digest,
        "artifact_digests": digests,
    })
    incident_history = _failure_incidents(scoped_events)
    gaps = _gaps(goal, tasks, handoff, incident_history)
    task_counts = _task_counts(tasks)
    diagnostics = [
        {
            "type": "task_record_missing",
            "task_id": str(task.get("id") or ""),
        }
        for task in tasks
        if task.get("missing")
    ]
    dossier = {
        "schema_version": SCHEMA_VERSION,
        "is_derived_projection": True,
        "run_id": canonical_run_id,
        "requested_run_id": requested,
        "goal_id": goal_id,
        "generated_at": generated_at,
        "source_fingerprint": source_fingerprint,
        "freshness": {
            "status": "incomplete" if diagnostics else "ready",
            "source_event_count": len(scoped_events),
            "last_event_id": scoped_events[-1].id,
            "last_event_at": scoped_events[-1].ts,
            "diagnostics": diagnostics,
        },
        "goal": goal,
        "roadmap": {
            "feature": feature,
            "task_order": task_ids,
            "dependencies": {
                row["id"]: row.get("blocked_by", []) for row in tasks
            },
            "task_map_refs": _values_for_keys(
                scoped_events,
                {"task_map_ref", "task_map_snapshot_ref"},
            ),
            "goal_claim_refs": _values_for_keys(
                scoped_events,
                {"goal_claim_set_ref", "goal_claim_ref"},
            ),
            "workflow_anchor_task_ids": workflow_anchor_task_ids,
        },
        "state": {
            "task_counts": task_counts,
            "tasks": tasks,
            "progress": progress,
            "handoff": handoff,
        },
        "evidence_index": _evidence_index(scoped_events),
        "gaps": gaps,
        "incident_history": incident_history,
        "closure": closure,
        "operations": operations,
        "source_manifest": source_manifest,
    }
    return redact_obj(dossier)


def write_goal_dossier_projection(state_dir: Path, dossier: dict[str, Any]) -> Path:
    """Atomically materialize the disposable JSON projection."""

    run_id = _safe_run_id(str(dossier.get("run_id") or ""))
    path = (
        Path(state_dir)
        / "projections"
        / "goals"
        / run_id
        / f"{SCHEMA_VERSION}.json"
    )
    atomic_write_text(
        path,
        json.dumps(redact_obj(dossier), ensure_ascii=False, indent=2) + "\n",
    )
    return path


def goal_dossier_view(
    dossier: dict[str, Any],
    *,
    section: str = "",
    preview: bool = False,
) -> dict[str, Any]:
    """Return a bounded API view without rebuilding or mutating the Dossier."""

    metadata = {
        "schema_version": dossier.get("schema_version"),
        "is_derived_projection": True,
        "run_id": dossier.get("run_id"),
        "goal_id": dossier.get("goal_id"),
        "generated_at": dossier.get("generated_at"),
        "source_fingerprint": dossier.get("source_fingerprint"),
        "freshness": dossier.get("freshness"),
    }
    if preview:
        goal = dossier.get("goal") if isinstance(dossier.get("goal"), dict) else {}
        state = dossier.get("state") if isinstance(dossier.get("state"), dict) else {}
        closure = dossier.get("closure") if isinstance(dossier.get("closure"), dict) else {}
        return redact_obj({
            **metadata,
            "view": "preview",
            "goal": {
                "objective": goal.get("objective"),
                "status": goal.get("status"),
                "delivery_phase": goal.get("delivery_phase"),
                "completion_gate_status": goal.get("completion_gate_status"),
            },
            "task_counts": state.get("task_counts") or {},
            "gap_count": len(dossier.get("gaps") or []),
            "evidence_count": len(dossier.get("evidence_index") or []),
            "operation_count": len(dossier.get("operations") or []),
            "closure_status": closure.get("status"),
        })
    normalized = str(section or "").strip()
    if not normalized:
        return redact_obj(dossier)
    allowed = {
        "goal",
        "roadmap",
        "state",
        "evidence_index",
        "gaps",
        "incident_history",
        "closure",
        "operations",
        "source_manifest",
    }
    if normalized not in allowed:
        raise GoalDossierError(f"unknown dossier section: {normalized!r}")
    return redact_obj({
        **metadata,
        "view": "section",
        "section": normalized,
        "data": dossier.get(normalized),
    })


def render_goal_dossier_markdown(dossier: dict[str, Any]) -> str:
    """Render the structured projection without re-reading runtime state."""

    goal = dossier.get("goal") if isinstance(dossier.get("goal"), dict) else {}
    state = dossier.get("state") if isinstance(dossier.get("state"), dict) else {}
    counts = state.get("task_counts") if isinstance(state.get("task_counts"), dict) else {}
    gaps = dossier.get("gaps") if isinstance(dossier.get("gaps"), list) else []
    incidents = (
        dossier.get("incident_history")
        if isinstance(dossier.get("incident_history"), list)
        else []
    )
    closure = dossier.get("closure") if isinstance(dossier.get("closure"), dict) else {}
    operations = dossier.get("operations") if isinstance(dossier.get("operations"), list) else []
    evidence = dossier.get("evidence_index") if isinstance(dossier.get("evidence_index"), list) else []
    lines = [
        f"# Goal Dossier: {dossier.get('run_id', '')}",
        "",
        f"- Schema: `{dossier.get('schema_version', '')}`",
        f"- Goal: `{dossier.get('goal_id', '')}`",
        f"- Status: `{goal.get('status', 'unknown')}`",
        f"- Completion gate: `{goal.get('completion_gate_status', 'unknown')}`",
        f"- Delivery phase: `{goal.get('delivery_phase', 'unknown')}`",
        f"- Source fingerprint: `{dossier.get('source_fingerprint', '')}`",
        f"- Generated at: `{dossier.get('generated_at', '')}`",
        "",
        "## Objective",
        "",
        str(goal.get("objective") or "(not recorded)"),
        "",
        "## State",
        "",
        f"- Tasks: {counts.get('total', 0)} total, {counts.get('terminal', 0)} terminal, {counts.get('open', 0)} open",
        f"- Open feedback: {goal.get('open_feedback_count', 0)}",
        f"- Pending handoffs: {goal.get('pending_handoff_count', 0)}",
        f"- Operations: {len(operations)}",
        f"- Failure incidents: {len(incidents)}",
        "",
        "## Tasks",
        "",
        "| Task | Status | Owner | Dependencies |",
        "|---|---|---|---|",
    ]
    for task in state.get("tasks", []):
        if not isinstance(task, dict):
            continue
        dependencies = ", ".join(task.get("blocked_by") or []) or "-"
        lines.append(
            f"| `{task.get('id', '')}` | {task.get('status', '')} | "
            f"{task.get('assigned_to') or '-'} | {dependencies} |"
        )
    lines.extend(["", "## Gaps", ""])
    if gaps:
        for gap in gaps:
            if isinstance(gap, dict):
                lines.append(
                    f"- `{gap.get('type', 'gap')}`: {gap.get('summary', '')} "
                    f"(source: `{gap.get('source_event_id', '')}`)"
                )
    else:
        lines.append("- None")
    lines.extend(["", "## Incident History", ""])
    if incidents:
        for incident in incidents:
            if not isinstance(incident, dict):
                continue
            lines.append(
                f"- `{incident.get('status', 'active')}` "
                f"`{incident.get('event_type', '')}` x{incident.get('count', 0)}: "
                f"{incident.get('summary', '')}"
            )
    else:
        lines.append("- None")
    lines.extend([
        "",
        "## Closure",
        "",
        f"- Status: `{closure.get('status', 'idle')}`",
        f"- Completion event: `{closure.get('completion_event_id', '')}`",
        f"- Lifecycle events: {closure.get('lifecycle_count', 0)}",
        "",
        "## Evidence",
        "",
    ])
    if evidence:
        for item in evidence:
            if isinstance(item, dict):
                lines.append(
                    f"- `{item.get('ref', '')}` ({item.get('event_type', '')}, "
                    f"event `{item.get('event_id', '')}`)"
                )
    else:
        lines.append("- None")
    return "\n".join(lines).rstrip() + "\n"


def write_goal_dossier_markdown(out: Path, dossier: dict[str, Any]) -> Path:
    out = Path(out)
    atomic_write_text(out, render_goal_dossier_markdown(redact_obj(dossier)))
    return out


def _safe_run_id(run_id: str) -> str:
    try:
        return validate_run_id(run_id)
    except RunArchiveError as exc:
        raise GoalDossierError(str(exc)) from exc


def _event_source(event: ZfEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "ts": event.ts,
        "actor": event.actor,
        "task_id": event.task_id,
        "payload": redact_obj(event.payload),
        "causation_id": event.causation_id,
        "correlation_id": event.correlation_id,
        "origin": event.origin,
    }


def _goal_id(events: Iterable[ZfEvent]) -> str:
    for event in reversed(list(events)):
        payload = event.payload if isinstance(event.payload, dict) else {}
        result = payload.get("goal_closure_result")
        result = result if isinstance(result, dict) else {}
        for value in (
            payload.get("goal_id"),
            payload.get("feature_id"),
            payload.get("pdd_id"),
            result.get("goal_id"),
        ):
            if str(value or "").strip():
                return str(value).strip()
    return ""


def _task_ids(events: Iterable[ZfEvent]) -> list[str]:
    result: list[str] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        candidates: list[Any] = [event.task_id, payload.get("task_id")]
        for key in ("task_ids", "canonical_task_ids", "children"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        for candidate in candidates:
            if isinstance(candidate, dict):
                candidate = candidate.get("task_id") or candidate.get("id")
            task_id = str(candidate or "").strip()
            if task_id and task_id not in result:
                result.append(task_id)
    return result


def _task_rows(state_dir: Path, task_ids: list[str]) -> list[dict[str, Any]]:
    store = TaskStore(state_dir / "kanban.json")
    rows: list[dict[str, Any]] = []
    for task_id in task_ids:
        task = store.get(task_id)
        if task is None:
            rows.append({"id": task_id, "status": "unknown", "missing": True})
            continue
        if is_workflow_fanout_anchor_task(task):
            continue
        rows.append(asdict(task))
    return redact_obj(rows)


def _workflow_anchor_task_ids(state_dir: Path, task_ids: list[str]) -> list[str]:
    store = TaskStore(state_dir / "kanban.json")
    return [
        task_id
        for task_id in task_ids
        if (task := store.get(task_id)) is not None
        and is_workflow_fanout_anchor_task(task)
    ]


def _feature_row(state_dir: Path, goal_id: str) -> dict[str, Any]:
    if not goal_id:
        return {}
    feature = FeatureStore(state_dir / "feature_list.json").get(goal_id)
    return redact_obj(asdict(feature)) if feature is not None else {}


def _operations(
    state_dir: Path,
    task_ids: list[str],
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task_id in task_ids:
        projection = project_task_operations(state_dir, task_id, events=events)
        candidates = list(projection.get("operations") or []) + list(
            projection.get("workflow_operations") or []
        )
        for row in candidates:
            if not isinstance(row, dict):
                continue
            identity = str(row.get("operation_id") or row.get("dispatch_id") or "")
            identity = identity or _digest(row)
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(row)
    return redact_obj(rows)


def _task_counts(tasks: list[dict[str, Any]]) -> dict[str, int]:
    terminal = sum(1 for task in tasks if task.get("status") in TERMINAL_STATES)
    return {
        "total": len(tasks),
        "terminal": terminal,
        "open": len(tasks) - terminal,
    }


def _gaps(
    goal: dict[str, Any],
    tasks: list[dict[str, Any]],
    handoff: dict[str, Any],
    incidents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for task in tasks:
        if task.get("status") not in TERMINAL_STATES:
            gaps.append({
                "type": "task_not_terminal",
                "task_id": str(task.get("id") or ""),
                "summary": f"task status is {task.get('status', 'unknown')}",
                "source_event_id": "",
            })
    for finding in handoff.get("open_feedback") or []:
        if isinstance(finding, dict):
            gaps.append({
                "type": "open_feedback",
                "task_id": str(finding.get("task_id") or ""),
                "summary": f"feedback {finding.get('finding_id', '')} remains open",
                "source_event_id": str(finding.get("last_event_id") or ""),
            })
    for incident in incidents:
        if incident.get("status") != "active":
            continue
        gaps.append({
            "type": "failure_incident",
            "incident_id": str(incident.get("incident_id") or ""),
            "event_type": str(incident.get("event_type") or ""),
            "task_id": str(incident.get("task_id") or ""),
            "summary": str(incident.get("summary") or ""),
            "occurrence_count": int(incident.get("count") or 0),
            "source_event_id": str(incident.get("last_event_id") or ""),
        })
    if goal.get("completion_gate_status") in {"blocked", "rejected"}:
        gaps.append({
            "type": "completion_gate",
            "summary": f"completion gate is {goal.get('completion_gate_status')}",
            "source_event_id": str(goal.get("source_event_id") or ""),
        })
    return redact_obj(gaps)


def _failure_incidents(events: list[ZfEvent]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for index, event in enumerate(events):
        if event.type not in _FAILURE_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = str(event.task_id or payload.get("task_id") or "")
        scope_id = _incident_scope_id(event)
        fingerprint = failure_fingerprint(event)
        key = (scope_id, event.type, fingerprint)
        summary = str(
            payload.get("reason")
            or payload.get("message")
            or payload.get("summary")
            or event.type
        )
        incident = grouped.get(key)
        if incident is None:
            incident = {
                "incident_id": "incident-" + hashlib.sha256(
                    "\0".join(key).encode("utf-8")
                ).hexdigest()[:16],
                "status": "active",
                "event_type": event.type,
                "task_id": task_id,
                "scope_id": scope_id,
                "failure_fingerprint": fingerprint,
                "summary": summary,
                "count": 0,
                "first_event_id": event.id,
                "first_event_at": event.ts,
                "last_event_id": event.id,
                "last_event_at": event.ts,
                "resolved_by_event_id": "",
                "resolved_by_event_type": "",
                "_last_index": index,
            }
            grouped[key] = incident
            order.append(key)
        incident["count"] = int(incident["count"]) + 1
        incident["summary"] = summary
        incident["last_event_id"] = event.id
        incident["last_event_at"] = event.ts
        incident["_last_index"] = index

    for key in order:
        incident = grouped[key]
        allowed = _FAILURE_SETTLEMENT_EVENTS.get(
            str(incident["event_type"]), frozenset(),
        )
        for event in events[int(incident["_last_index"]) + 1:]:
            if not is_successful_run_terminal(event) and event.type not in allowed:
                continue
            if (
                not is_successful_run_terminal(event)
                and _incident_scope_id(event) != incident["scope_id"]
            ):
                continue
            incident["status"] = "resolved"
            incident["resolved_by_event_id"] = event.id
            incident["resolved_by_event_type"] = event.type
            break
        incident.pop("_last_index", None)
    return redact_obj([grouped[key] for key in order])


def _incident_scope_id(event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    for value in (
        event.task_id,
        payload.get("task_id"),
        payload.get("pdd_id"),
        payload.get("feature_id"),
        payload.get("goal_id"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return "run"


def _evidence_index(events: Iterable[ZfEvent]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key, value in _walk(payload):
            if not _is_ref_key(key):
                continue
            for ref in _strings(value):
                identity = (event.id, ref)
                if identity in seen:
                    continue
                seen.add(identity)
                rows.append({
                    "ref": ref,
                    "key": key,
                    "event_id": event.id,
                    "event_type": event.type,
                    "task_id": str(event.task_id or payload.get("task_id") or ""),
                })
    return rows


def _source_refs(events: Iterable[ZfEvent]) -> tuple[list[str], list[str]]:
    refs: list[str] = []
    digests: list[str] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key, value in _walk(payload):
            target = digests if _is_digest_key(key) else refs if _is_ref_key(key) else None
            if target is None:
                continue
            for item in _strings(value):
                if item not in target:
                    target.append(item)
    return refs, digests


def _values_for_keys(events: Iterable[ZfEvent], keys: set[str]) -> list[str]:
    values: list[str] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key, value in _walk(payload):
            if key not in keys:
                continue
            for item in _strings(value):
                if item not in values:
                    values.append(item)
    return values


def _walk(value: Any, *, prefix: str = "", depth: int = 0) -> Iterable[tuple[str, Any]]:
    if depth > 6:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            text = str(key)
            yield text, child
            yield from _walk(child, prefix=text, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, prefix=prefix, depth=depth + 1)


def _is_ref_key(key: str) -> bool:
    normalized = key.lower()
    if any(
        secret in normalized
        for secret in (
            "api_key",
            "authorization",
            "cookie",
            "password",
            "private_key",
            "secret",
            "token",
        )
    ):
        return False
    return normalized == "ref" or normalized.endswith("_ref") or normalized.endswith("_refs")


def _is_digest_key(key: str) -> bool:
    normalized = key.lower()
    return normalized == "digest" or normalized.endswith("_digest")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            for text in _strings(item):
                if text not in result:
                    result.append(text)
        return result
    if isinstance(value, dict):
        return [
            str(item).strip()
            for key in ("ref", "path", "uri", "digest")
            if (item := value.get(key)) and str(item).strip()
        ]
    return []


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = [
    "GoalDossierError",
    "SCHEMA_VERSION",
    "build_goal_dossier",
    "goal_dossier_view",
    "render_goal_dossier_markdown",
    "write_goal_dossier_markdown",
    "write_goal_dossier_projection",
]
