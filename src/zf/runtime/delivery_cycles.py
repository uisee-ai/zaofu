"""Delivery cycle projections for delivery-trace.v1.

The projection is intentionally read-only: it derives delivery / autoresearch
cycles from already-loaded events and phase rollups, and never writes runtime
state or re-judges kernel decisions.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.autoresearch_cycles import build_autoresearch_cycles

EventSlice = Sequence[tuple[int, ZfEvent]]

_REPLAN_TYPES = {
    "plan.insight.discovered",
    "research.probe.requested",
    "research.probe.completed",
    "reflection.recorded",
    "replan.proposal.created",
    "replan.contract_eval.requested",
    "replan.contract_eval.completed",
    "replan.contract_eval.adoption_blocked",
    "replan.adoption.prepared",
    "replan.adoption.completed",
    "replan.adoption.stale_rejected",
    "replan.adoption.awaiting_owner",
    "replan.adoption.owner_rejected",
    "replan.owner_decision.approved",
    "replan.owner_decision.deferred",
    "replan.owner_decision.rejected",
}
_REWORK_TYPES = {"task.rework.requested", "task.fix_spawned"}
_SHIP_PREFIXES = ("ship.", "candidate.")
_FANOUT_TYPES = {
    "fanout.started",
    "fanout.child.dispatched",
    "fanout.aggregate.completed",
    "fanout.cancelled",
    "fanout.timed_out",
}
_TERMINAL_STATUS = {
    "completed", "failed", "rejected", "skipped", "blocked", "adopted",
    "shipped", "owner_rejected",
}


def build_delivery_cycles(
    *,
    events: EventSlice,
    phases: list[dict[str, Any]],
    feature_id: str,
    task_ids: set[str],
    task_map_ref: str = "",
    replan_contract_gate: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return delivery cycles and autoresearch cycles for delivery-trace.v1."""

    cycles = _phase_cycles(phases)
    cycles.extend(_fanout_cycles(events, feature_id, task_ids, task_map_ref))
    cycles.extend(_rework_cycles(events, feature_id, task_ids))
    cycles.extend(_replan_cycles(events, feature_id, task_ids, task_map_ref, replan_contract_gate))
    cycles.extend(_ship_cycles(events, feature_id, task_ids))

    return redact_obj({
        "cycles": _dedupe_cycles(cycles)[-160:],
        "autoresearch_cycles": build_autoresearch_cycles(
            events=events,
            feature_id=feature_id,
            task_ids=task_ids,
        ),
    })


def _phase_cycles(phases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for phase in sorted(phases, key=lambda p: int(p.get("order") or 0)):
        phase_id = str(phase.get("phase_id") or "default")
        out.append({
            "cycle_id": f"phase:{phase_id}",
            "kind": "planned_phase",
            "phase": phase_id,
            "order": int(phase.get("order") or 0),
            "status": str(phase.get("status") or "waiting"),
            "gate": str((phase.get("eval") or {}).get("verdict") or "pending"),
            "task_ids": _clean_str_list(phase.get("task_ids") or []),
            "task_count": int(phase.get("task_count") or 0),
            "done_count": int(phase.get("done_count") or 0),
            "completion_rate": phase.get("completion_rate"),
            "pass_rate": phase.get("pass_rate"),
            "rework_count": int(phase.get("rework_count") or 0),
            "paused_count": int(phase.get("paused_count") or 0),
            "evidence_refs": [],
            "events": [],
        })
    return out


def _fanout_cycles(
    events: EventSlice,
    feature_id: str,
    task_ids: set[str],
    task_map_ref: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[int, ZfEvent]]] = {}
    for seq, event in events:
        if event.type not in _FANOUT_TYPES:
            continue
        payload = _payload(event)
        fanout_id = str(payload.get("fanout_id") or "").strip()
        if not fanout_id or not _event_linked(event, payload, feature_id, task_ids, task_map_ref):
            continue
        groups.setdefault(fanout_id, []).append((seq, event))

    out: list[dict[str, Any]] = []
    for fanout_id, group in groups.items():
        compact = _compact_events(group)
        first_payload = _payload(group[0][1])
        status = _latest_status(
            compact,
            default=str(first_payload.get("status") or "running"),
            terminal_by_type={
                "fanout.aggregate.completed": "completed",
                "fanout.cancelled": "cancelled",
                "fanout.timed_out": "timed_out",
            },
        )
        out.append({
            "cycle_id": f"fanout:{fanout_id}",
            "kind": "fanout",
            "phase": str(first_payload.get("stage_id") or ""),
            "status": status,
            "topology": str(first_payload.get("topology") or ""),
            "task_ids": _cycle_task_ids(group, task_ids),
            "fanout_id": fanout_id,
            "started_at": _first_ts(group),
            "ended_at": _last_ts(group) if status in _TERMINAL_STATUS else "",
            "evidence_refs": _evidence_refs(group),
            "events": compact,
        })
    return out


def _rework_cycles(events: EventSlice, feature_id: str, task_ids: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seq, event in events:
        payload = _payload(event)
        if event.type not in _REWORK_TYPES or not _event_linked(event, payload, feature_id, task_ids, ""):
            continue
        task_id = str(event.task_id or payload.get("task_id") or "")
        out.append({
            "cycle_id": f"rework:{event.id or seq}",
            "kind": "rework",
            "status": "requested",
            "trigger": str(payload.get("reason") or payload.get("trigger") or event.type),
            "task_ids": [task_id] if task_id else [],
            "started_at": event.ts,
            "ended_at": "",
            "evidence_refs": _evidence_refs([(seq, event)]),
            "events": _compact_events([(seq, event)]),
        })
    return out


def _replan_cycles(
    events: EventSlice,
    feature_id: str,
    task_ids: set[str],
    task_map_ref: str,
    replan_contract_gate: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[int, ZfEvent]]] = {}
    for seq, event in events:
        if event.type not in _REPLAN_TYPES:
            continue
        payload = _payload(event)
        nested_eval = payload.get("eval") if isinstance(payload.get("eval"), dict) else {}
        if not _replan_linked(payload, nested_eval, feature_id, task_map_ref):
            continue
        key = str(
            payload.get("candidate_task_map_ref")
            or payload.get("new_task_map_ref")
            or payload.get("task_map_ref")
            or nested_eval.get("candidate_task_map_ref")
            or nested_eval.get("new_task_map_ref")
            or nested_eval.get("task_map_ref")
            or payload.get("eval_id")
            or payload.get("request_id")
            or payload.get("proposal_id")
            or payload.get("artifact_id")
            or payload.get("insight_id")
            or nested_eval.get("eval_id")
            or event.id
        )
        groups.setdefault(key, []).append((seq, event))

    out: list[dict[str, Any]] = []
    for key, group in groups.items():
        latest_payload = _payload(group[-1][1])
        nested_eval = latest_payload.get("eval") if isinstance(latest_payload.get("eval"), dict) else {}
        eval_payload = nested_eval or latest_payload
        status = _replan_status(group, replan_contract_gate)
        out.append({
            "cycle_id": f"replan:{key}",
            "kind": "replan",
            "status": status,
            "gate": str(eval_payload.get("decision") or status),
            "trigger": str(eval_payload.get("trigger_failure_class") or latest_payload.get("reason") or "replan"),
            "task_ids": _cycle_task_ids(group, task_ids),
            "started_at": _first_ts(group),
            "ended_at": _last_ts(group) if status in _TERMINAL_STATUS or status in {"ready_to_adopt", "evaluated"} else "",
            "old_task_map_ref": str(eval_payload.get("old_task_map_ref") or ""),
            "new_task_map_ref": str(
                eval_payload.get("new_task_map_ref")
                or latest_payload.get("candidate_task_map_ref")
                or latest_payload.get("task_map_ref")
                or ""
            ),
            "insight_ref": _last_payload_value(group, "source_insight_ref", "insight_ref"),
            "proposal_ref": _last_payload_value(group, "proposal_ref"),
            "request_id": _last_payload_value(group, "request_id"),
            "artifact_ref": _artifact_ref(eval_payload) or _artifact_ref(latest_payload),
            "evidence_refs": _evidence_refs(group),
            "events": _compact_events(group),
        })

    if not out and replan_contract_gate and replan_contract_gate.get("latest_eval"):
        latest = dict(replan_contract_gate.get("latest_eval") or {})
        key = str(latest.get("eval_id") or latest.get("new_task_map_ref") or "latest")
        out.append({
            "cycle_id": f"replan:{key}",
            "kind": "replan",
            "status": str(replan_contract_gate.get("status") or "evaluated"),
            "gate": str(latest.get("decision") or ""),
            "trigger": str(latest.get("trigger_failure_class") or "replan"),
            "task_ids": [],
            "old_task_map_ref": str(latest.get("old_task_map_ref") or ""),
            "new_task_map_ref": str(latest.get("new_task_map_ref") or ""),
            "artifact_ref": str(latest.get("artifact_ref") or ""),
            "evidence_refs": _clean_str_list([latest.get("event_id"), latest.get("artifact_ref")]),
            "events": [],
        })
    return out


def _ship_cycles(events: EventSlice, feature_id: str, task_ids: set[str]) -> list[dict[str, Any]]:
    group = [
        (seq, event)
        for seq, event in events
        if event.type.startswith(_SHIP_PREFIXES)
        and _event_linked(event, _payload(event), feature_id, task_ids, "")
    ]
    if not group:
        return []
    terminal = {
        "ship.completed": "shipped",
        "ship.done": "shipped",
        "ship.blocked": "blocked",
        "ship.conflict": "blocked",
        "ship.failed": "failed",
        "candidate.quality.passed": "passed",
        "candidate.quality.failed": "failed",
        "candidate.integration.completed": "integrated",
    }
    compact = _compact_events(group)
    status = _latest_status(compact, default="candidate", terminal_by_type=terminal)
    return [{
        "cycle_id": "ship:latest",
        "kind": "ship",
        "status": status,
        "task_ids": _cycle_task_ids(group, task_ids),
        "started_at": _first_ts(group),
        "ended_at": _last_ts(group) if status in _TERMINAL_STATUS or status == "integrated" else "",
        "evidence_refs": _evidence_refs(group),
        "events": compact,
    }]


def _event_linked(
    event: ZfEvent,
    payload: dict[str, Any],
    feature_id: str,
    task_ids: set[str],
    task_map_ref: str,
) -> bool:
    task_id = str(event.task_id or payload.get("task_id") or "")
    if task_id and task_id in task_ids:
        return True
    for raw in payload.get("task_ids") or payload.get("completed_task_ids") or []:
        if str(raw) in task_ids:
            return True
    payload_feature = str(payload.get("feature_id") or payload.get("pdd_id") or "")
    if feature_id and payload_feature == feature_id:
        return True
    if task_map_ref and str(payload.get("task_map_ref") or "") == task_map_ref:
        return True
    return not feature_id and not task_ids


def _replan_linked(
    payload: dict[str, Any],
    nested_eval: dict[str, Any],
    feature_id: str,
    task_map_ref: str,
) -> bool:
    if feature_id and str(payload.get("feature_id") or nested_eval.get("feature_id") or "") == feature_id:
        return True
    refs = {
        str(payload.get("task_map_ref") or ""),
        str(payload.get("old_task_map_ref") or ""),
        str(payload.get("new_task_map_ref") or ""),
        str(payload.get("candidate_task_map_ref") or ""),
        str(payload.get("expected_current_task_map_ref") or ""),
        str(nested_eval.get("old_task_map_ref") or ""),
        str(nested_eval.get("new_task_map_ref") or ""),
        str(nested_eval.get("candidate_task_map_ref") or ""),
        str(nested_eval.get("expected_current_task_map_ref") or ""),
    }
    if task_map_ref and task_map_ref in refs:
        return True
    # Plan-insight / probe / proposal events are often refs-only before a
    # candidate task-map exists. Keep them visible for synthetic/project-level
    # traces where no narrower feature/task-map filter is applied.
    if not feature_id and not task_map_ref:
        return True
    return False


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _clean_str_list(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _cycle_task_ids(group: list[tuple[int, ZfEvent]], known_task_ids: set[str]) -> list[str]:
    values: list[str] = []
    for _seq, event in group:
        payload = _payload(event)
        values.extend([event.task_id, payload.get("task_id")])
        values.extend(payload.get("task_ids") or [])
    cleaned = _clean_str_list(values)
    return [task_id for task_id in cleaned if not known_task_ids or task_id in known_task_ids]


def _compact_events(group: list[tuple[int, ZfEvent]]) -> list[dict[str, Any]]:
    return [{
        "seq": seq,
        "event_id": event.id,
        "event_type": event.type,
        "task_id": str(event.task_id or _payload(event).get("task_id") or ""),
        "ts": event.ts,
        "status": _status_from_event(event),
    } for seq, event in group][-40:]


def _evidence_refs(group: list[tuple[int, ZfEvent]]) -> list[str]:
    refs: list[Any] = []
    for _seq, event in group:
        payload = _payload(event)
        refs.append(event.id)
        refs.append(payload.get("artifact_ref"))
        refs.append(payload.get("candidate_ref"))
        refs.append(payload.get("source_insight_ref"))
        refs.append(payload.get("insight_ref"))
        refs.append(payload.get("proposal_ref"))
        refs.append(payload.get("candidate_task_map_ref"))
        refs.extend(payload.get("evidence_refs") or [])
        nested_refs = payload.get("refs") if isinstance(payload.get("refs"), dict) else {}
        refs.append(nested_refs.get("artifact_ref"))
    return _clean_str_list(refs)[-60:]


def _last_payload_value(group: list[tuple[int, ZfEvent]], *keys: str) -> str:
    for _seq, event in reversed(group):
        payload = _payload(event)
        for key in keys:
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    return ""


def _first_ts(group: list[tuple[int, ZfEvent]]) -> str:
    return str(group[0][1].ts or "") if group else ""


def _last_ts(group: list[tuple[int, ZfEvent]]) -> str:
    return str(group[-1][1].ts or "") if group else ""


def _latest_status(
    compact_events: list[dict[str, Any]],
    *,
    default: str,
    terminal_by_type: dict[str, str],
) -> str:
    status = default
    for item in compact_events:
        event_type = str(item.get("event_type") or "")
        status = terminal_by_type.get(event_type) or str(item.get("status") or status)
    return status or default


def _status_from_event(event: ZfEvent) -> str:
    payload = _payload(event)
    if payload.get("status"):
        return str(payload.get("status"))
    tail = event.type.rsplit(".", 1)[-1]
    if tail == "requested":
        return "requested"
    if tail in {"accepted", "rejected", "started", "completed", "failed", "skipped"}:
        return tail
    return tail


def _replan_status(
    group: list[tuple[int, ZfEvent]],
    replan_contract_gate: dict[str, Any] | None,
) -> str:
    status = str((replan_contract_gate or {}).get("status") or "")
    if status == "none":
        status = ""
    for _seq, event in group:
        if event.type == "replan.contract_eval.adoption_blocked":
            status = "blocked"
        elif event.type == "replan.adoption.completed":
            status = "adopted"
        elif event.type == "replan.adoption.stale_rejected":
            status = "stale_rejected"
        elif event.type == "replan.adoption.owner_rejected":
            status = "owner_rejected"
        elif event.type == "replan.adoption.awaiting_owner":
            status = "awaiting_owner"
        elif event.type == "replan.adoption.prepared" and not status:
            status = "prepared"
        elif event.type == "replan.contract_eval.completed" and not status:
            payload = _payload(event)
            status = str(payload.get("decision") or "evaluated")
        elif event.type == "replan.contract_eval.requested" and not status:
            status = "eval_requested"
        elif event.type == "replan.proposal.created" and not status:
            status = "proposed"
        elif event.type == "replan.owner_decision.approved":
            status = "owner_approved"
        elif event.type == "replan.owner_decision.deferred":
            status = "owner_deferred"
        elif event.type == "replan.owner_decision.rejected":
            status = "owner_rejected"
        elif event.type == "research.probe.completed" and not status:
            status = "researched"
        elif event.type == "research.probe.requested" and not status:
            status = "research_requested"
        elif event.type == "plan.insight.discovered" and not status:
            status = "insight"
    return status or "observed"


def _artifact_ref(payload: dict[str, Any]) -> str:
    direct = str(payload.get("artifact_ref") or "").strip()
    if direct:
        return direct
    refs = payload.get("refs")
    if isinstance(refs, dict):
        return str(refs.get("artifact_ref") or "").strip()
    return ""


def _dedupe_cycles(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for cycle in cycles:
        cycle_id = str(cycle.get("cycle_id") or "")
        if cycle_id and cycle_id in seen:
            continue
        if cycle_id:
            seen.add(cycle_id)
        out.append(cycle)
    return out
