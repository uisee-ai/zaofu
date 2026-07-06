"""Read-only human handoff summary projection."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.feature.store import FeatureStore
from zf.core.security.redaction import redact_obj
from zf.core.state.state_packet import StatePacket, packet_to_dict
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.long_horizon import build_resume_packet
from zf.runtime.state_packet_projector import StatePacketProjector


EventSlice = Sequence[tuple[int, ZfEvent]]

_STAGE_AFTER_EVENT: dict[str, tuple[str, str]] = {
    "task.dispatched": ("implement", "dev.build.done"),
    "dev.build.done": ("static_gate", "static_gate.passed"),
    "static_gate.passed": ("review", "review.approved"),
    "review.approved": ("test", "test.passed"),
    "verify.passed": ("judge", "judge.passed"),
    "test.passed": ("judge", "judge.passed"),
    "judge.passed": ("ship", ""),
}


def project_handoff_summary(
    state_dir: Path,
    task_id: str,
    *,
    task: Task | None = None,
    task_events: EventSlice | None = None,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    dispatch_id: str = "",
    now: datetime | None = None,
    index: Any = None,
) -> dict[str, Any]:
    """Build handoff-summary.v1 without mutating runtime state."""

    now = now or datetime.now(timezone.utc)
    task = task or _load_task(state_dir, task_id)
    dispatch_id = dispatch_id or _latest_dispatch_id(task, task_events or [])
    state_packet = _safe_state_packet(state_dir, task_id, dispatch_id=dispatch_id)
    resume_packet = _safe_resume_packet(
        state_dir,
        task_id,
        dispatch_id=dispatch_id,
        config=config,
        project_root=project_root,
        index=index,
    )
    packet_dict = packet_to_dict(state_packet) if state_packet else {}
    resume_ref = _resume_packet_ref(state_dir, task_id, dispatch_id)
    completed = _completed(packet_dict, resume_packet)
    missing = _records(resume_packet.get("missing_evidence"))
    blockers = _blockers(task, packet_dict, resume_packet)
    evidence_refs = _evidence_refs(packet_dict, resume_packet)
    changed_files = _strings(resume_packet.get("changed_files"))
    risks = _risks(packet_dict, resume_packet, task_events or [])
    source_event_ids = _source_event_ids(
        packet_dict=packet_dict,
        resume_packet=resume_packet,
        completed=completed,
        missing=missing,
        evidence_refs=evidence_refs,
    )
    objective = (
        _text(packet_dict.get("objective"))
        or _text(resume_packet.get("objective"))
        or _task_objective(task)
    )
    current_state = (
        _text(resume_packet.get("current_state"))
        or (task.status if task else "")
        or _text(packet_dict.get("current_stage"))
    )
    event_stage, event_next = _stage_from_events(task_events or [])
    next_required_event = (
        _text(resume_packet.get("next_required_event"))
        or event_next
        or _text(packet_dict.get("next_event"))
    )
    next_required_action = _text(resume_packet.get("next_required_action"))
    quality = _handoff_quality(
        objective=objective,
        completed=completed,
        evidence_refs=evidence_refs,
        changed_files=changed_files,
        risks=risks,
        next_required_event=next_required_event,
        next_required_action=next_required_action,
        task_events=task_events or [],
    )
    return redact_obj({
        "schema_version": "handoff-summary.v1",
        "generated_at": now.isoformat(),
        "task_id": task_id,
        "objective": objective,
        "current_state": current_state,
        "current_stage": event_stage or _text(packet_dict.get("current_stage")),
        "owner": _owner(packet_dict, task),
        "completed": completed,
        "missing_evidence": missing,
        "blockers": blockers,
        "next_required_event": next_required_event,
        "next_required_action": next_required_action,
        "do_not_repeat": _strings(resume_packet.get("do_not_repeat")),
        "evidence_refs": evidence_refs,
        "changed_files": changed_files,
        "risks": risks,
        "quality": quality,
        "resume_packet_ref": resume_ref,
        "source_event_ids": source_event_ids,
        "empty": not bool(objective or completed or missing or blockers or next_required_event),
    })


def _safe_state_packet(
    state_dir: Path,
    task_id: str,
    *,
    dispatch_id: str = "",
) -> StatePacket | None:
    try:
        task_store = TaskStore(state_dir / "kanban.json")
        feature_store = FeatureStore(state_dir / "feature_list.json")
        event_log = EventLog(state_dir / "events.jsonl")
        projector = StatePacketProjector(
            state_dir=state_dir,
            task_store=task_store,
            feature_store=feature_store,
            event_log=event_log,
        )
        return projector.project(task_id=task_id, run_id=dispatch_id)
    except Exception:
        return None


def _safe_resume_packet(
    state_dir: Path,
    task_id: str,
    *,
    dispatch_id: str = "",
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    index: Any = None,
) -> dict[str, Any]:
    try:
        packet = build_resume_packet(
            state_dir,
            task_id,
            dispatch_id=dispatch_id,
            config=config,
            project_root=project_root,
            index=index,
        )
        return packet if isinstance(packet, dict) else {}
    except Exception:
        return {}


def _load_task(state_dir: Path, task_id: str) -> Task | None:
    try:
        return TaskStore(state_dir / "kanban.json").get(task_id)
    except Exception:
        return None


def _latest_dispatch_id(task: Task | None, events: EventSlice) -> str:
    if task is not None and getattr(task, "active_dispatch_id", ""):
        return str(task.active_dispatch_id)
    for _, event in reversed(list(events)):
        payload = event.payload if isinstance(event.payload, dict) else {}
        value = _text(payload.get("dispatch_id")) or _text(payload.get("active_dispatch_id"))
        if value:
            return value
    return ""


def _stage_from_events(events: EventSlice) -> tuple[str, str]:
    latest = ("", "")
    for _, event in events:
        spec = _STAGE_AFTER_EVENT.get(event.type)
        if spec:
            latest = spec
    return latest


def _completed(
    packet_dict: dict[str, Any],
    resume_packet: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for value in _strings(packet_dict.get("completed")):
        items.append({"kind": "milestone", "summary": value})
    for item in _records(resume_packet.get("completed_evidence")):
        items.append({
            "kind": _text(item.get("type")) or "event",
            "event_id": _text(item.get("event_id")),
            "summary": _text(item.get("summary")) or _text(item.get("type")),
        })
    return _dedupe_records(items, keys=("kind", "event_id", "summary"))


def _blockers(
    task: Task | None,
    packet_dict: dict[str, Any],
    resume_packet: dict[str, Any],
) -> list[str]:
    values: list[str] = []
    if task is not None:
        values.extend(_strings(getattr(task, "blocked_by", [])))
        if getattr(task, "blocked_reason", ""):
            values.append(str(task.blocked_reason))
    values.extend(_strings(packet_dict.get("blocked_by")))
    values.extend(_strings(resume_packet.get("blockers")))
    return list(dict.fromkeys(item for item in values if item))


def _evidence_refs(
    packet_dict: dict[str, Any],
    resume_packet: dict[str, Any],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in _records(packet_dict.get("evidence")):
        refs.append({
            "kind": _text(item.get("kind")),
            "path": _text(item.get("path")),
            "status": _text(item.get("status")),
            "event_id": _text(item.get("event_id")),
        })
    for key in ("accepted_artifact_refs", "stale_artifact_refs", "missing_artifact_refs"):
        for item in _records(resume_packet.get(key)):
            refs.append({"kind": key, **item})
    return _dedupe_records(refs, keys=("kind", "path", "event_id", "status"))


def _risks(
    packet_dict: dict[str, Any],
    resume_packet: dict[str, Any],
    events: EventSlice,
) -> list[str]:
    values: list[str] = []
    values.extend(_strings(packet_dict.get("risks")))
    values.extend(_strings(resume_packet.get("risks")))
    values.extend(_strings(resume_packet.get("residual_risks")))
    for _, event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        values.extend(_strings(payload.get("risks")))
        values.extend(_strings(payload.get("residual_risks")))
        risk = _text(payload.get("risk")) or _text(payload.get("known_risk"))
        if risk:
            values.append(risk)
    return list(dict.fromkeys(item for item in values if item))


def _handoff_quality(
    *,
    objective: str,
    completed: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    changed_files: list[str],
    risks: list[str],
    next_required_event: str,
    next_required_action: str,
    task_events: EventSlice,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, hint: str) -> None:
        checks.append({
            "name": name,
            "passed": bool(passed),
            "hint": "" if passed else hint,
        })

    add("summary_present", bool(objective or completed), "handoff summary 缺少目标或已完成摘要")
    add(
        "test_evidence_present",
        _has_test_evidence(completed, evidence_refs, task_events),
        "缺少测试/验证证据；下游不应盲目接手",
    )
    add("risks_recorded", bool(risks), "缺少 residual risks / known risks")
    add(
        "next_action_clear",
        bool(next_required_action or next_required_event),
        "缺少 next_required_action 或 next_required_event",
    )
    add(
        "work_refs_present",
        bool(changed_files or evidence_refs),
        "缺少 changed_files 或 evidence refs",
    )
    gaps = [
        {"field": item["name"], "reason": item["hint"]}
        for item in checks
        if not item["passed"]
    ]
    return {
        "schema_version": "handoff-quality.v1",
        "status": "accepted" if not gaps else "needs_handoff_fix",
        "score": len(checks) - len(gaps),
        "max_score": len(checks),
        "checks": checks,
        "gaps": gaps,
    }


def _has_test_evidence(
    completed: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    events: EventSlice,
) -> bool:
    test_tokens = ("test", "verify", "pytest", "ruff", "static_gate", "passed")
    for item in evidence_refs:
        text = " ".join(
            _text(item.get(key)).lower()
            for key in ("kind", "path", "status", "summary")
        )
        if any(token in text for token in test_tokens):
            return True
    for item in completed:
        text = " ".join(
            _text(item.get(key)).lower()
            for key in ("kind", "type", "summary")
        )
        if any(token in text for token in test_tokens):
            return True
    for _, event in events:
        if event.type in {"test.passed", "verify.passed", "static_gate.passed"}:
            return True
        payload = event.payload if isinstance(event.payload, dict) else {}
        text = " ".join(
            _text(payload.get(key)).lower()
            for key in ("test", "tests", "verification", "check", "checks", "command")
        )
        if any(token in text for token in test_tokens):
            return True
    return False


def _owner(packet_dict: dict[str, Any], task: Task | None) -> dict[str, str]:
    owner = _record(packet_dict.get("owner"))
    return {
        "role": _text(owner.get("role")),
        "instance_id": _text(owner.get("instance_id")) or (task.assigned_to if task else "") or "",
        "role_context": _text(owner.get("role_context")),
        "next_owner": _text(packet_dict.get("next_owner")),
    }


def _task_objective(task: Task | None) -> str:
    if task is None:
        return ""
    contract = getattr(task, "contract", None)
    return (
        _text(getattr(contract, "behavior", ""))
        or _text(getattr(contract, "verification", ""))
        or _text(getattr(task, "title", ""))
    )


def _resume_packet_ref(state_dir: Path, task_id: str, dispatch_id: str) -> str:
    candidates = []
    if dispatch_id:
        candidates.extend([
            state_dir / "briefings" / task_id / dispatch_id / "resume-packet.json",
            state_dir / "resume_packets" / f"{task_id}.{dispatch_id}.json",
        ])
    candidates.append(state_dir / "resume_packets" / f"{task_id}.json")
    for path in candidates:
        if path.exists():
            return str(path)
    return ""


def _source_event_ids(
    *,
    packet_dict: dict[str, Any],
    resume_packet: dict[str, Any],
    completed: list[dict[str, Any]],
    missing: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
) -> list[str]:
    values: list[str] = []
    values.extend(_strings(resume_packet.get("source_event_ids")))
    for collection in (completed, missing, evidence_refs):
        for item in collection:
            values.append(_text(item.get("event_id")))
            values.extend(_strings(item.get("evidence_refs")))
    for item in _records(packet_dict.get("evidence")):
        values.append(_text(item.get("event_id")))
    return list(dict.fromkeys(item for item in values if item))


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            result.append(item)
        elif is_dataclass(item):
            result.append(asdict(item))
    return result


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None and str(item)]
    return [str(value)]


def _dedupe_records(
    items: list[dict[str, Any]],
    *,
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = tuple(_text(item.get(part)) for part in keys)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
