"""Context recovery and skill provenance projections for Supervisor."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj


CONTEXT_RECOVERY_SCHEMA_VERSION = "context.recovery.projection.v0"
SKILL_PROVENANCE_SCHEMA_VERSION = "skill.provenance.projection.v0"

_CONTEXT_STATE_BY_EVENT = {
    "worker.context.precompact": "precompact_observed",
    "worker.context.snapshot_requested": "snapshot_requested",
    "worker.context.compact.requested": "compact_requested",
    "worker.context.compacted": "compacted",
    "worker.context.compact.failed": "compact_failed",
    "worker.recovery.insufficient": "insufficient_recovery_context",
    "worker.recovery.blocked": "recovery_blocked",
    "recovery.contract.rehydrate.requested": "rehydrate_requested",
    "recovery.contract.rehydrated": "rehydrated",
    "context.compact.requested": "compact_requested",
    "context.compacted": "compacted",
    "context.compact.failed": "compact_failed",
    "context.recovery.started": "recovery_started",
    "context.recovery.rehydrated": "rehydrated",
    "context.recovery.insufficient": "insufficient_recovery_context",
    "context.recovery.blocked": "recovery_blocked",
    "context.recovery.retry_budget_exhausted": "retry_budget_exhausted",
}
_RETRY_EXHAUSTED_EVENTS = {
    "context.recovery.retry_budget_exhausted",
    "worker.recovery.retry_budget_exhausted",
}


def context_recovery_projection(events: list[ZfEvent]) -> dict[str, Any]:
    by_instance: dict[str, dict[str, Any]] = {}
    transitions: list[dict[str, Any]] = []
    retry_exhausted = False
    for event in events:
        if event.type not in _CONTEXT_STATE_BY_EVENT and event.type not in _RETRY_EXHAUSTED_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        instance_id = _context_instance_id(event, payload)
        state = _CONTEXT_STATE_BY_EVENT.get(event.type, "retry_budget_exhausted")
        retry_exhausted = retry_exhausted or event.type in _RETRY_EXHAUSTED_EVENTS
        if str(payload.get("retry_budget_exhausted") or "").lower() == "true":
            retry_exhausted = True
        row = by_instance.setdefault(instance_id, {
            "instance_id": instance_id,
            "current_state": "",
            "last_event_id": "",
            "last_event_type": "",
            "last_event_at": "",
            "task_id": event.task_id or str(payload.get("task_id") or ""),
            "retry_budget_exhausted": False,
            "failures": 0,
        })
        row["current_state"] = state
        row["last_event_id"] = event.id
        row["last_event_type"] = event.type
        row["last_event_at"] = event.ts
        row["task_id"] = event.task_id or str(payload.get("task_id") or row.get("task_id") or "")
        if state in {"compact_failed", "insufficient_recovery_context", "recovery_blocked"}:
            row["failures"] = int(row.get("failures") or 0) + 1
        if state == "retry_budget_exhausted":
            row["retry_budget_exhausted"] = True
        transitions.append({
            "event_id": event.id,
            "ts": event.ts,
            "type": event.type,
            "instance_id": instance_id,
            "state": state,
            "task_id": event.task_id or str(payload.get("task_id") or ""),
        })
    states = Counter(str(row.get("current_state") or "unknown") for row in by_instance.values())
    return redact_obj({
        "schema_version": CONTEXT_RECOVERY_SCHEMA_VERSION,
        "is_derived_projection": True,
        "summary": {
            "instances": len(by_instance),
            "retry_budget_exhausted": retry_exhausted or any(
                bool(row.get("retry_budget_exhausted")) for row in by_instance.values()
            ),
            "by_state": dict(sorted(states.items())),
        },
        "by_instance": dict(sorted(by_instance.items())),
        "recent_transitions": transitions[-50:],
    })


def skill_provenance_projection(state_dir: Path) -> dict[str, Any]:
    path = Path(state_dir) / "skills.lock.json"
    if not path.exists():
        return {
            "schema_version": SKILL_PROVENANCE_SCHEMA_VERSION,
            "is_derived_projection": True,
            "status": "missing",
            "path": str(path),
            "summary": {"total": 0, "by_status": {}, "by_source": {}, "by_role": {}},
            "entries": [],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "schema_version": SKILL_PROVENANCE_SCHEMA_VERSION,
            "is_derived_projection": True,
            "status": "invalid",
            "path": str(path),
            "error": str(exc),
            "summary": {"total": 0, "by_status": {}, "by_source": {}, "by_role": {}},
            "entries": [],
        }
    raw_entries = data.get("skills") if isinstance(data, dict) else []
    entries = [item for item in raw_entries if isinstance(item, dict)]
    by_status: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    by_role: Counter[str] = Counter()
    warnings = 0
    collisions = 0
    compact_entries: list[dict[str, Any]] = []
    for item in entries:
        status = str(item.get("status") or "unknown")
        source = str(item.get("source_name") or item.get("source") or "unknown")
        role = str(item.get("instance_id") or item.get("role") or "unknown")
        by_status[status] += 1
        by_source[source] += 1
        by_role[role] += 1
        item_warnings = item.get("warnings") if isinstance(item.get("warnings"), list) else []
        item_collisions = (
            item.get("collision_candidates")
            if isinstance(item.get("collision_candidates"), list) else []
        )
        warnings += len(item_warnings)
        collisions += len(item_collisions)
        compact_entries.append({
            "role": str(item.get("role") or ""),
            "instance_id": str(item.get("instance_id") or ""),
            "backend": str(item.get("backend") or ""),
            "task_id": str(item.get("task_id") or ""),
            "run_id": str(item.get("run_id") or ""),
            "name": str(item.get("name") or ""),
            "source_name": str(item.get("source_name") or ""),
            "source": str(item.get("source") or ""),
            "sha256": str(item.get("sha256") or ""),
            "status": status,
            "override": bool(item.get("override")),
            "warnings": item_warnings[:5],
            "collision_candidates": item_collisions[:5],
            "materialized_to": str(item.get("materialized_to") or ""),
        })
    return redact_obj({
        "schema_version": SKILL_PROVENANCE_SCHEMA_VERSION,
        "is_derived_projection": True,
        "status": "ok",
        "path": str(path),
        "lockfile_version": data.get("version") if isinstance(data, dict) else None,
        "generated_at": str(data.get("generated_at") or "") if isinstance(data, dict) else "",
        "summary": {
            "total": len(entries),
            "warnings": warnings,
            "collisions": collisions,
            "overrides": sum(1 for item in entries if bool(item.get("override"))),
            "by_status": dict(sorted(by_status.items())),
            "by_source": dict(sorted(by_source.items())),
            "by_role": dict(sorted(by_role.items())),
        },
        "entries": compact_entries[-200:],
    })


def _context_instance_id(event: ZfEvent, payload: dict[str, Any]) -> str:
    for key in ("instance_id", "role_instance", "worker_id", "role"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    actor = str(event.actor or "").strip()
    return actor or "unknown"


__all__ = [
    "CONTEXT_RECOVERY_SCHEMA_VERSION",
    "SKILL_PROVENANCE_SCHEMA_VERSION",
    "context_recovery_projection",
    "skill_provenance_projection",
]
