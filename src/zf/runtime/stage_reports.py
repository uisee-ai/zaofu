"""Deterministic stage report artifacts for long-running workflows."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.stage_report_io import (
    STAGE_REPORT_DIR as _STAGE_REPORT_DIR,
    read_latest_stage_report,
    render_stage_report_markdown,
    write_stage_report,
)


STAGE_REPORT_SCHEMA_VERSION = "stage-report.v1"
STAGE_REPORT_DIR = _STAGE_REPORT_DIR

_STAGE_EVENTS: dict[str, tuple[str, ...]] = {
    "scan": (
        "zaofu.refactor.review.ready",
        "zaofu.refactor.review.blocked",
        "artifact.manifest.published",
        "artifact.manifest.rejected",
        "artifact.manifest.blocked",
    ),
    "plan": (
        "arch.proposal.done",
        "design.critique.done",
        "zaofu.refactor.plan.ready",
        "zaofu.refactor.plan.blocked",
        "replan.contract_eval.completed",
        "replan.adoption.completed",
    ),
    "task-map": (
        "product_delivery.task_map.accepted",
        "product_delivery.task_map.rejected",
        "task.graph.updated",
    ),
    "impl": (
        "dev.build.done",
        "dev.failed",
        "dev.blocked",
        "static_gate.passed",
        "static_gate.failed",
        "static_gate.skipped",
        "fanout.child.completed",
        "fanout.child.failed",
        "fanout.timed_out",
        "fanout.aggregate.completed",
    ),
    "review": (
        "review.approved",
        "review.rejected",
        "review.child.completed",
        "review.child.failed",
    ),
    "verify": (
        "verify.passed",
        "verify.failed",
        "test.passed",
        "test.failed",
        "judge.passed",
        "judge.failed",
        "verify.child.completed",
        "verify.child.failed",
    ),
}

_EVENT_TO_STAGE = {
    event_type: stage
    for stage, event_types in _STAGE_EVENTS.items()
    for event_type in event_types
}


def stage_for_event(event_type: str) -> str:
    return _EVENT_TO_STAGE.get(event_type, "")


def project_stage_report_for_event(
    state_dir: Path,
    event: ZfEvent,
    *,
    events: list[ZfEvent] | None = None,
) -> dict[str, Any] | None:
    stage = stage_for_event(event.type)
    if not stage:
        return None
    event_log = EventLog(Path(state_dir) / "events.jsonl")
    event_list = events if events is not None else event_log.read_all()
    report = build_stage_report(
        state_dir,
        stage,
        trigger_event=event,
        events=event_list,
    )
    write_stage_report(state_dir, report)
    return report


def build_stage_report(
    state_dir: Path,
    stage: str,
    *,
    trigger_event: ZfEvent | None = None,
    events: list[ZfEvent] | None = None,
    tasks: list[Task] | None = None,
) -> dict[str, Any]:
    state_dir = Path(state_dir)
    event_list = list(events if events is not None else _read_events(state_dir))
    task_list = list(tasks if tasks is not None else _read_tasks(state_dir))
    stage_events = _events_for_stage(stage, event_list)
    trigger = trigger_event or (stage_events[-1] if stage_events else None)
    scoped_events = _scope_events(stage, stage_events, trigger)
    return {
        "schema_version": STAGE_REPORT_SCHEMA_VERSION,
        "is_derived_projection": True,
        "stage": stage,
        "generated_at": _stable_generated_at(scoped_events),
        "trigger_event": _event_ref(trigger),
        "stage_inputs": _stage_inputs(scoped_events),
        "summary": _summary(stage, scoped_events, task_list),
        "tasks": _task_rows(task_list, scoped_events),
        "events": [_event_ref(event) for event in scoped_events],
        "fanout": _fanout_summary(scoped_events),
        "artifact_refs": _artifact_refs(scoped_events),
        "gate_evidence": _gate_evidence(scoped_events),
        "gaps": _gaps(scoped_events),
        "rework": _rework_summary(scoped_events),
        "next_action": _next_action(stage, scoped_events),
    }


def _events_for_stage(stage: str, events: list[ZfEvent]) -> list[ZfEvent]:
    expected = set(_STAGE_EVENTS.get(stage, ()))
    return [event for event in events if event.type in expected]


def _scope_events(
    stage: str,
    events: list[ZfEvent],
    trigger_event: ZfEvent | None,
) -> list[ZfEvent]:
    if trigger_event is None:
        return events
    if trigger_event.task_id:
        scoped = [
            event for event in events
            if event.task_id in {"", None, trigger_event.task_id}
            or _payload(event).get("task_id") == trigger_event.task_id
        ]
        if scoped:
            return scoped
    if stage == "impl":
        fanout_id = str(_payload(trigger_event).get("fanout_id") or "")
        if fanout_id:
            scoped = [
                event for event in events
                if str(_payload(event).get("fanout_id") or "") == fanout_id
            ]
            if scoped:
                return scoped
    return events


def _summary(
    stage: str,
    events: list[ZfEvent],
    tasks: list[Task],
) -> dict[str, Any]:
    next_action = _next_action(stage, events)
    return {
        "stage": stage,
        "task_count": len(_task_rows(tasks, events)),
        "event_count": len(events),
        "event_types": _counts(event.type for event in events),
        "next_action": next_action,
        "blocked": next_action in {"blocked", "needs_operator"},
    }


def _task_rows(tasks: list[Task], events: list[ZfEvent]) -> list[dict[str, Any]]:
    task_ids = {
        task_id
        for event in events
        for task_id in (
            event.task_id,
            str(_payload(event).get("task_id") or ""),
        )
        if task_id
    }
    rows: list[dict[str, Any]] = []
    for task in tasks:
        if task_ids and task.id not in task_ids:
            continue
        row = asdict(task)
        row["recent_event_types"] = [
            event.type for event in events
            if event.task_id == task.id or _payload(event).get("task_id") == task.id
        ][-8:]
        rows.append(row)
    return rows


def _fanout_summary(events: list[ZfEvent]) -> dict[str, Any]:
    children: list[dict[str, str]] = []
    for event in events:
        if not event.type.startswith("fanout.child."):
            continue
        payload = _payload(event)
        children.append({
            "event_id": event.id,
            "type": event.type,
            "fanout_id": str(payload.get("fanout_id") or ""),
            "child_id": str(payload.get("child_id") or ""),
            "task_id": str(payload.get("task_id") or event.task_id or ""),
            "attempt": str(payload.get("attempt") or ""),
            "log_ref": str(payload.get("log_ref") or payload.get("log_path") or ""),
            "reason": str(payload.get("reason") or ""),
        })
    return {
        "children": children,
        "completed": sum(1 for child in children if child["type"].endswith(".completed")),
        "failed": sum(1 for child in children if child["type"].endswith(".failed")),
        "without_output": [
            child for child in children
            if child["type"].endswith(".failed") or not child["child_id"]
        ],
    }


def _artifact_refs(events: list[ZfEvent]) -> list[str]:
    refs: list[str] = []
    for event in events:
        payload = _payload(event)
        for key in (
            "artifact_ref",
            "artifact_path",
            "task_map_ref",
            "source_index_ref",
            "handoff_ref",
            "report_ref",
            "path",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value:
                refs.append(value)
        for key in ("artifact_refs", "artifacts", "verification_refs"):
            value = payload.get(key)
            if isinstance(value, list):
                refs.extend(str(item) for item in value if item)
    return sorted(set(refs))


def _gate_evidence(events: list[ZfEvent]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        if event.type.startswith(("static_gate.", "review.", "verify.", "test.", "judge.")):
            rows.append({
                "event_id": event.id,
                "event_type": event.type,
                "task_id": event.task_id or str(_payload(event).get("task_id") or ""),
                "reason": str(_payload(event).get("reason") or ""),
                "verdict": str(_payload(event).get("verdict") or ""),
            })
    return rows


def _stage_inputs(events: list[ZfEvent]) -> dict[str, Any]:
    refs: list[str] = []
    skills: list[str] = []
    scopes: list[str] = []
    constraints: list[str] = []
    modules: list[str] = []
    for event in events:
        payload = _payload(event)
        refs.extend(_string_values(payload, "prompt_ref", "prompt_path", "input_ref"))
        skills.extend(_list_or_string(payload.get("skills")))
        constraints.extend(_list_or_string(payload.get("constraints")))
        scopes.extend(_list_or_string(payload.get("scope")))
        modules.extend(_list_or_string(payload.get("modules")))
    return {
        "prompt_refs": sorted(set(refs)),
        "skills": sorted(set(skills)),
        "constraints": sorted(set(constraints)),
        "scope": sorted(set(scopes)),
        "modules": sorted(set(modules)),
    }


def _gaps(events: list[ZfEvent]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in events:
        if not (
            event.type.endswith(".blocked")
            or event.type.endswith(".failed")
            or event.type.endswith(".rejected")
        ):
            continue
        payload = _payload(event)
        rows.append({
            "event_id": event.id,
            "event_type": event.type,
            "task_id": event.task_id or str(payload.get("task_id") or ""),
            "reason": str(payload.get("reason") or ""),
            "artifact_ref": str(
                payload.get("artifact_ref")
                or payload.get("feedback_artifact")
                or payload.get("report_ref")
                or ""
            ),
        })
    return rows


def _rework_summary(events: list[ZfEvent]) -> dict[str, Any]:
    rejected = [
        event for event in events
        if event.type.endswith(".rejected") or event.type.endswith(".failed")
    ]
    return {
        "rejected_count": len(rejected),
        "items": [{
            "event_id": event.id,
            "event_type": event.type,
            "task_id": event.task_id or str(_payload(event).get("task_id") or ""),
            "reason": str(_payload(event).get("reason") or ""),
            "rework_target": _rework_target(event),
        } for event in rejected],
    }


def _next_action(stage: str, events: list[ZfEvent]) -> str:
    event_types = {event.type for event in events}
    if not events:
        return "blocked"
    if event_types.intersection({
        "review.rejected",
        "verify.failed",
        "test.failed",
        "judge.failed",
        "product_delivery.task_map.rejected",
        "zaofu.refactor.plan.blocked",
        "zaofu.refactor.review.blocked",
    }):
        return "rework"
    if event_types.intersection({"fanout.child.failed", "fanout.timed_out"}):
        return "needs_operator"
    if stage in {"scan", "plan", "task-map", "impl", "review", "verify"}:
        return "proceed"
    return "blocked"


def _rework_target(event: ZfEvent) -> str:
    if event.type in {"review.rejected", "verify.failed", "test.failed", "judge.failed"}:
        return "impl"
    if event.type == "product_delivery.task_map.rejected":
        return "plan"
    return ""


def _event_ref(event: ZfEvent | None) -> dict[str, Any]:
    if event is None:
        return {}
    return {
        "id": event.id,
        "type": event.type,
        "task_id": event.task_id or str(_payload(event).get("task_id") or ""),
        "actor": event.actor,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
        "payload": _redacted_payload(event),
    }


def _redacted_payload(event: ZfEvent) -> dict[str, Any]:
    payload = _payload(event)
    return {
        key: value
        for key, value in payload.items()
        if key.lower() not in {"api_key", "token", "secret", "password"}
    }


def _string_values(payload: dict[str, Any], *keys: str) -> list[str]:
    out: list[str] = []
    for key in keys:
        out.extend(_list_or_string(payload.get(key)))
    return out


def _list_or_string(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value:
        return [value]
    return []


def _read_events(state_dir: Path) -> list[ZfEvent]:
    try:
        return EventLog(Path(state_dir) / "events.jsonl").read_all()
    except Exception:
        return []


def _read_tasks(state_dir: Path) -> list[Task]:
    try:
        return TaskStore(Path(state_dir) / "kanban.json").list_all()
    except Exception:
        return []


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _counts(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = str(value)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _stable_generated_at(events: list[ZfEvent]) -> str:
    if events:
        return str(events[-1].ts)
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "STAGE_REPORT_SCHEMA_VERSION",
    "build_stage_report",
    "project_stage_report_for_event",
    "read_latest_stage_report",
    "render_stage_report_markdown",
    "stage_for_event",
    "write_stage_report",
]
