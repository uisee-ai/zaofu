"""Current/stale fanout identity projection."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


FANOUT_IDENTITY_SCHEMA_VERSION = "fanout-identity.v1"

FANOUT_IDENTITY_EVENT_TYPES = frozenset({
    "fanout.started",
    "fanout.child.queued",
    "fanout.child.dispatched",
    "fanout.child.completed",
    "fanout.child.failed",
    "fanout.child.stale_completion",
    "fanout.aggregate.started",
    "fanout.aggregate.completed",
    "fanout.timed_out",
    "fanout.cancelled",
})

_CHILD_EVENT_TYPES = frozenset({
    "fanout.child.queued",
    "fanout.child.dispatched",
    "fanout.child.completed",
    "fanout.child.failed",
})

_AGGREGATE_EVENT_TYPES = frozenset({
    "fanout.aggregate.started",
    "fanout.aggregate.completed",
    "fanout.timed_out",
    "fanout.cancelled",
})


@dataclass
class FanoutIdentity:
    fanout_instance_id: str
    fanout_id: str
    logical_key: str
    current: bool
    stage_id: str = ""
    topology: str = ""
    target_ref: str = ""
    task_id: str = ""
    pdd_id: str = ""
    feature_id: str = ""
    wave_alias: str = ""
    wave_index: int | None = None
    wave_total: int | None = None
    attempt: int | None = None
    started_event_id: str = ""
    superseded_by: str = ""
    stale_reason: str = ""
    child_events: list[dict[str, str]] = field(default_factory=list)
    aggregate_events: list[dict[str, str]] = field(default_factory=list)
    stale_events: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FanoutCurrentStatus:
    fanout_id: str
    known: bool
    current: bool
    logical_key: str = ""
    superseded_by: str = ""
    stale_reason: str = ""


def read_fanout_identities(state_dir: Path) -> dict[str, Any]:
    events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    return build_fanout_identity_projection(events)


def fanout_current_status(
    events: Iterable[ZfEvent],
    fanout_id: str,
) -> FanoutCurrentStatus:
    """Return whether a fanout instance is still current.

    Legacy logs may contain child events without a corresponding
    ``fanout.started`` record. Treat those as current unless the projection
    can explicitly prove that a newer equivalent fanout superseded them.
    """
    if not fanout_id:
        return FanoutCurrentStatus(fanout_id="", known=False, current=True)
    projection = build_fanout_identity_projection(events)
    for item in projection.get("instances", []) or []:
        if str(item.get("fanout_id") or "") != fanout_id:
            continue
        return FanoutCurrentStatus(
            fanout_id=fanout_id,
            known=True,
            current=bool(item.get("current")),
            logical_key=str(item.get("logical_key") or ""),
            superseded_by=str(item.get("superseded_by") or ""),
            stale_reason=str(item.get("stale_reason") or ""),
        )
    return FanoutCurrentStatus(fanout_id=fanout_id, known=False, current=True)


def build_fanout_identity_projection(events: Iterable[ZfEvent]) -> dict[str, Any]:
    identities: dict[str, FanoutIdentity] = {}
    current_by_key: dict[str, str] = {}
    stale_events: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []

    for event in events:
        if event.type not in FANOUT_IDENTITY_EVENT_TYPES:
            continue
        payload = _payload(event)
        fanout_id = str(payload.get("fanout_id") or "")
        if not fanout_id:
            issues.append(_issue(event, "", "missing_fanout_id"))
            continue

        if event.type == "fanout.started":
            identity = _identity_from_started(event, payload, fanout_id)
            previous_id = current_by_key.get(identity.logical_key)
            if previous_id and previous_id in identities:
                previous = identities[previous_id]
                previous.current = False
                previous.superseded_by = fanout_id
                previous.stale_reason = "superseded_by_latest_fanout"
            identities[fanout_id] = identity
            current_by_key[identity.logical_key] = fanout_id
            continue

        identity = identities.get(fanout_id)
        if identity is None:
            issue = _issue(event, fanout_id, "fanout_event_without_started")
            issues.append(issue)
            stale_events.append(_event_ref(event, stale_reason=issue["reason"]))
            continue

        if event.type == "fanout.child.stale_completion":
            ref = _event_ref(
                event,
                child_id=str(payload.get("child_id") or ""),
                stale_reason=_reason(payload) or "explicit_stale_completion",
            )
            identity.stale_events.append(ref)
            stale_events.append(ref)
            continue

        if not identity.current:
            reason = identity.stale_reason or "fanout_instance_not_current"
            ref = _event_ref(
                event,
                child_id=str(payload.get("child_id") or ""),
                stale_reason=reason,
            )
            identity.stale_events.append(ref)
            stale_events.append(ref)
            continue

        if event.type in _CHILD_EVENT_TYPES:
            identity.child_events.append(_event_ref(
                event,
                child_id=str(payload.get("child_id") or ""),
            ))
        elif event.type in _AGGREGATE_EVENT_TYPES:
            identity.aggregate_events.append(_event_ref(event))

    identity_list = sorted(
        (identity.to_dict() for identity in identities.values()),
        key=lambda item: (
            str(item.get("logical_key") or ""),
            "0" if item.get("current") else "1",
            str(item.get("fanout_id") or ""),
        ),
    )
    return {
        "schema_version": FANOUT_IDENTITY_SCHEMA_VERSION,
        "is_derived_projection": True,
        "summary": _summary(identity_list, stale_events, issues),
        "current": [item for item in identity_list if item["current"]],
        "stale": [item for item in identity_list if not item["current"]],
        "instances": identity_list,
        "stale_events": stale_events,
        "issues": issues,
    }


def _identity_from_started(
    event: ZfEvent,
    payload: dict[str, Any],
    fanout_id: str,
) -> FanoutIdentity:
    task_id = str(event.task_id or payload.get("task_id") or "")
    stage_id = str(payload.get("stage_id") or "")
    target_ref = str(payload.get("target_ref") or "")
    pdd_id = str(payload.get("pdd_id") or "")
    feature_id = str(payload.get("feature_id") or "")
    logical_key = _logical_key(
        stage_id=stage_id,
        target_ref=target_ref,
        task_id=task_id,
        pdd_id=pdd_id,
        feature_id=feature_id,
    )
    return FanoutIdentity(
        fanout_instance_id=fanout_id,
        fanout_id=fanout_id,
        logical_key=logical_key,
        current=True,
        stage_id=stage_id,
        topology=str(payload.get("topology") or ""),
        target_ref=target_ref,
        task_id=task_id,
        pdd_id=pdd_id,
        feature_id=feature_id,
        wave_alias=_first_payload_string(payload, "wave_alias", "wave_id"),
        wave_index=_first_payload_int(payload, "wave_index", "index"),
        wave_total=_first_payload_int(payload, "wave_total", "expected_total", "total"),
        attempt=_first_payload_int(payload, "attempt", "wave_attempt", "fanout_attempt"),
        started_event_id=event.id,
    )


def _logical_key(
    *,
    stage_id: str,
    target_ref: str,
    task_id: str,
    pdd_id: str,
    feature_id: str,
) -> str:
    parts = [
        stage_id,
        target_ref,
        task_id,
        pdd_id,
        feature_id,
    ]
    return "|".join(part for part in parts if part) or "global"


def _summary(
    instances: list[dict[str, Any]],
    stale_events: list[dict[str, str]],
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    current_count = sum(1 for item in instances if item.get("current") is True)
    return {
        "total_instances": len(instances),
        "current_instances": current_count,
        "stale_instances": len(instances) - current_count,
        "stale_event_count": len(stale_events),
        "issue_count": len(issues),
    }


def _event_ref(
    event: ZfEvent,
    *,
    child_id: str = "",
    stale_reason: str = "",
) -> dict[str, str]:
    payload = _payload(event)
    return {
        "event_id": event.id,
        "event_type": event.type,
        "fanout_id": str(payload.get("fanout_id") or ""),
        "child_id": child_id,
        "task_id": str(event.task_id or payload.get("task_id") or ""),
        "reason": _reason(payload),
        "stale_reason": stale_reason,
    }


def _issue(event: ZfEvent, fanout_id: str, reason: str) -> dict[str, str]:
    return {
        "event_id": event.id,
        "event_type": event.type,
        "fanout_id": fanout_id,
        "reason": reason,
    }


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _first_payload_string(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_payload_int(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return int(stripped)
    return None


def _reason(payload: dict[str, Any]) -> str:
    for key in ("reason", "error", "message", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


__all__ = [
    "FANOUT_IDENTITY_SCHEMA_VERSION",
    "FanoutCurrentStatus",
    "build_fanout_identity_projection",
    "fanout_current_status",
    "read_fanout_identities",
]
