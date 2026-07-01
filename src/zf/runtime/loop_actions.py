"""Controlled action bridge for ``loop.v1`` candidates.

Loop actions are an intent surface: they append events that existing kernel
paths can consume. They never write TaskStore/Kanban truth directly.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.delivery_projection_common import dedupe, payload

EventSlice = Sequence[tuple[int, ZfEvent]]

LOOP_ACTION_REQUESTED = "loop.action.requested"
LOOP_ACTION_MAPPED = "loop.action.mapped"
LOOP_ACTION_REJECTED = "loop.action.rejected"


@dataclass(frozen=True)
class LoopActionRequest:
    loop_id: str
    candidate_id: str
    suggested_action: str
    idempotency_key: str = ""
    project_id: str = ""
    source: str = "web"


def request_loop_action(
    *,
    events: EventSlice,
    projection: dict[str, Any],
    writer: EventWriter,
    request: LoopActionRequest,
) -> dict[str, Any]:
    """Append a loop action request and its mapped downstream intent."""

    candidate = _find_candidate(projection, request.candidate_id, request.loop_id)
    if candidate is None:
        return {
            "ok": False,
            "status": "not_found",
            "reason": "loop candidate not found",
            "_status_code": 404,
        }
    loop = _find_loop(projection, str(candidate.get("loop_id") or request.loop_id))
    if loop is None:
        return {
            "ok": False,
            "status": "not_found",
            "reason": "loop not found",
            "_status_code": 404,
        }

    suggested_action = request.suggested_action or str(candidate.get("suggested_action") or "")
    action_id = _loop_action_id(
        request.project_id,
        str(candidate.get("candidate_id") or ""),
        suggested_action,
        request.idempotency_key,
    )
    source_events = _source_events(events, candidate, loop)
    task_ids = dedupe([
        *_string_list(candidate.get("task_ids")),
        *_string_list(loop.get("task_ids")),
        *[str(event.task_id or "") for event in source_events],
    ])
    task_ids = [item for item in task_ids if item]
    evidence_event_ids = dedupe([
        *_string_list(candidate.get("event_ids")),
        *_string_list(loop.get("event_ids")),
        *[str(event.id or "") for event in source_events],
    ])

    requested = writer.append(ZfEvent(
        type=LOOP_ACTION_REQUESTED,
        actor="web",
        task_id=task_ids[0] if task_ids else None,
        correlation_id=f"loop-action:{action_id}",
        payload={
            "action_id": action_id,
            "loop_id": loop.get("loop_id") or "",
            "candidate_id": candidate.get("candidate_id") or "",
            "suggested_action": suggested_action,
            "source_kind": candidate.get("source_kind") or "",
            "project_id": request.project_id,
            "idempotency_key": request.idempotency_key,
            "task_ids": task_ids,
            "evidence_refs": evidence_event_ids,
            "proposal_only": _proposal_only(suggested_action),
            "source": request.source,
        },
    ))

    mapped = _mapped_event(
        action_id=action_id,
        suggested_action=suggested_action,
        candidate=candidate,
        loop=loop,
        source_events=source_events,
        task_ids=task_ids,
        evidence_event_ids=evidence_event_ids,
        requested_event=requested,
    )
    if mapped is None:
        rejected = _reject(
            writer,
            requested_event=requested,
            action_id=action_id,
            candidate=candidate,
            loop=loop,
            suggested_action=suggested_action,
            task_id=task_ids[0] if task_ids else "",
            reason=f"unsupported_loop_action:{suggested_action or 'missing'}",
        )
        return _response(action_id, requested, rejected, status="rejected", reason="unsupported loop action")

    if mapped.get("reject_reason"):
        rejected = _reject(
            writer,
            requested_event=requested,
            action_id=action_id,
            candidate=candidate,
            loop=loop,
            suggested_action=suggested_action,
            task_id=task_ids[0] if task_ids else "",
            reason=str(mapped["reject_reason"]),
        )
        return _response(action_id, requested, rejected, status="rejected", reason=str(mapped["reject_reason"]))

    downstream = writer.append(ZfEvent(
        type=str(mapped["event_type"]),
        actor="web",
        task_id=str(mapped.get("task_id") or "") or None,
        payload=dict(mapped["payload"]),
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
    ))
    mapped_event = writer.append(ZfEvent(
        type=LOOP_ACTION_MAPPED,
        actor="web",
        task_id=str(mapped.get("task_id") or "") or None,
        payload={
            "action_id": action_id,
            "loop_id": loop.get("loop_id") or "",
            "candidate_id": candidate.get("candidate_id") or "",
            "suggested_action": suggested_action,
            "mapped_event_id": downstream.id,
            "mapped_event_type": downstream.type,
            "mapped_action": mapped.get("mapped_action") or "",
            "downstream_action_id": mapped.get("downstream_action_id") or "",
            "proposal_only": mapped.get("proposal_only", False),
            "evidence_refs": evidence_event_ids,
        },
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
    ))
    return _response(action_id, requested, mapped_event, mapped=downstream, status="mapped")


def _mapped_event(
    *,
    action_id: str,
    suggested_action: str,
    candidate: dict[str, Any],
    loop: dict[str, Any],
    source_events: list[ZfEvent],
    task_ids: list[str],
    evidence_event_ids: list[str],
    requested_event: ZfEvent,
) -> dict[str, Any] | None:
    action = suggested_action.strip()
    if action == "inspect_worker_liveness":
        return _worker_liveness_mapping(
            action_id=action_id,
            loop=loop,
            source_events=source_events,
            task_ids=task_ids,
            evidence_event_ids=evidence_event_ids,
        )
    if action in {"review_gate_evidence", "inspect_rework_route"}:
        return _autoresearch_mapping(
            action_id=action_id,
            action=action,
            mode="debug",
            expected_output="reflection",
            candidate=candidate,
            loop=loop,
            task_ids=task_ids,
            evidence_event_ids=evidence_event_ids,
        )
    if action == "harden_evidence_contract":
        return _autoresearch_mapping(
            action_id=action_id,
            action=action,
            mode="probe",
            expected_output="scenario_pack",
            candidate=candidate,
            loop=loop,
            task_ids=task_ids,
            evidence_event_ids=evidence_event_ids,
        )
    if action in {"review_fanout_barrier", "review_autoresearch_result", "inspect_loop"}:
        return _autoresearch_mapping(
            action_id=action_id,
            action=action,
            mode="probe",
            expected_output="reflection",
            candidate=candidate,
            loop=loop,
            task_ids=task_ids,
            evidence_event_ids=evidence_event_ids,
        )
    if action == "review_replan_contract":
        return _replan_contract_mapping(
            action_id=action_id,
            loop=loop,
            source_events=source_events,
            task_ids=task_ids,
            evidence_event_ids=evidence_event_ids,
            requested_event=requested_event,
        )
    return None


def _worker_liveness_mapping(
    *,
    action_id: str,
    loop: dict[str, Any],
    source_events: list[ZfEvent],
    task_ids: list[str],
    evidence_event_ids: list[str],
) -> dict[str, Any]:
    worker_id = _worker_target(source_events)
    task_id = task_ids[0] if task_ids else ""
    repair_action_id = f"ra-{_stable_id(action_id, worker_id, task_id)}"
    if worker_id:
        return {
            "event_type": "repair.action.requested",
            "task_id": task_id,
            "mapped_action": "restart_worker",
            "downstream_action_id": repair_action_id,
            "payload": {
                "action_id": repair_action_id,
                "kind": "restart_worker",
                "worker_id": worker_id,
                "task_id": task_id,
                "idempotency_key": f"repair:{action_id}",
                "reason": "loop inspect_worker_liveness",
                "source_loop_action_id": action_id,
                "loop_id": loop.get("loop_id") or "",
                "evidence_refs": evidence_event_ids,
            },
        }
    if task_id:
        return {
            "event_type": "repair.action.requested",
            "task_id": task_id,
            "mapped_action": "requeue_task",
            "downstream_action_id": repair_action_id,
            "payload": {
                "action_id": repair_action_id,
                "kind": "requeue_task",
                "task_id": task_id,
                "idempotency_key": f"repair:{action_id}",
                "reason": "loop inspect_worker_liveness fallback",
                "source_loop_action_id": action_id,
                "loop_id": loop.get("loop_id") or "",
                "evidence_refs": evidence_event_ids,
            },
        }
    return {"reject_reason": "missing_worker_or_task_target"}


def _autoresearch_mapping(
    *,
    action_id: str,
    action: str,
    mode: str,
    expected_output: str,
    candidate: dict[str, Any],
    loop: dict[str, Any],
    task_ids: list[str],
    evidence_event_ids: list[str],
) -> dict[str, Any]:
    return {
        "event_type": "autoresearch.loop.requested",
        "task_id": task_ids[0] if task_ids else "",
        "mapped_action": f"autoresearch:{mode}",
        "downstream_action_id": f"ar-{_stable_id(action_id, mode)}",
        "proposal_only": True,
        "payload": {
            "run_id": f"ar-loop-{_stable_id(action_id, mode)}",
            "mode": mode,
            "expected_output": expected_output,
            "proposal_only": True,
            "apply_policy": "proposal_only",
            "source_loop_action_id": action_id,
            "loop_id": loop.get("loop_id") or "",
            "candidate_id": candidate.get("candidate_id") or "",
            "suggested_action": action,
            "source_kind": candidate.get("source_kind") or "",
            "task_ids": task_ids,
            "evidence_refs": evidence_event_ids,
        },
    }


def _replan_contract_mapping(
    *,
    action_id: str,
    loop: dict[str, Any],
    source_events: list[ZfEvent],
    task_ids: list[str],
    evidence_event_ids: list[str],
    requested_event: ZfEvent,
) -> dict[str, Any]:
    candidate_ref = _first_payload_value(
        source_events,
        "candidate_task_map_ref",
        "new_task_map_ref",
        "proposal_ref",
        "task_map_ref",
    )
    if not candidate_ref:
        return {"reject_reason": "missing_candidate_task_map_ref"}
    return {
        "event_type": "replan.contract_eval.requested",
        "task_id": task_ids[0] if task_ids else "",
        "mapped_action": "replan.contract_eval",
        "downstream_action_id": f"replan-eval-{_stable_id(action_id, candidate_ref)}",
        "proposal_only": True,
        "payload": {
            "source_loop_action_id": action_id,
            "loop_id": loop.get("loop_id") or "",
            "candidate_task_map_ref": candidate_ref,
            "proposal_ref": candidate_ref,
            "expected_current_task_map_ref": _first_payload_value(source_events, "old_task_map_ref", "current_task_map_ref"),
            "task_ids": task_ids,
            "evidence_refs": evidence_event_ids,
            "proposal_only": True,
            "requested_event_id": requested_event.id,
        },
    }


def _reject(
    writer: EventWriter,
    *,
    requested_event: ZfEvent,
    action_id: str,
    candidate: dict[str, Any],
    loop: dict[str, Any],
    suggested_action: str,
    task_id: str,
    reason: str,
) -> ZfEvent:
    return writer.append(ZfEvent(
        type=LOOP_ACTION_REJECTED,
        actor="web",
        task_id=task_id or None,
        payload={
            "action_id": action_id,
            "loop_id": loop.get("loop_id") or "",
            "candidate_id": candidate.get("candidate_id") or "",
            "suggested_action": suggested_action,
            "reason": reason,
        },
        causation_id=requested_event.id,
        correlation_id=requested_event.correlation_id,
    ))


def _response(
    action_id: str,
    requested: ZfEvent,
    terminal: ZfEvent,
    *,
    status: str,
    reason: str = "",
    mapped: ZfEvent | None = None,
) -> dict[str, Any]:
    out = {
        "ok": status != "rejected",
        "status": status,
        "action_id": action_id,
        "request_event_id": requested.id,
        "terminal_event_id": terminal.id,
        "terminal_event_type": terminal.type,
        "_status_code": 202 if status != "rejected" else 409,
    }
    if mapped is not None:
        out.update({
            "mapped_event_id": mapped.id,
            "mapped_event_type": mapped.type,
        })
    if reason:
        out["reason"] = reason
    return out


def _find_candidate(projection: dict[str, Any], candidate_id: str, loop_id: str) -> dict[str, Any] | None:
    for item in projection.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        if candidate_id and item.get("candidate_id") != candidate_id:
            continue
        if loop_id and item.get("loop_id") != loop_id:
            continue
        return item
    return None


def _find_loop(projection: dict[str, Any], loop_id: str) -> dict[str, Any] | None:
    for item in projection.get("loops") or []:
        if isinstance(item, dict) and item.get("loop_id") == loop_id:
            return item
    return None


def _source_events(events: EventSlice, candidate: dict[str, Any], loop: dict[str, Any]) -> list[ZfEvent]:
    ids = set(_string_list(candidate.get("event_ids")) + _string_list(loop.get("event_ids")))
    return [event for _seq, event in events if event.id in ids]


def _worker_target(events: list[ZfEvent]) -> str:
    for event in events:
        data = payload(event)
        for key in ("worker_id", "instance_id", "role_instance", "role"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
        if event.actor:
            return str(event.actor)
    return ""


def _first_payload_value(events: list[ZfEvent], *keys: str) -> str:
    for event in events:
        data = payload(event)
        for key in keys:
            value = str(data.get(key) or "").strip()
            if value:
                return value
    return ""


def _proposal_only(action: str) -> bool:
    return action != "inspect_worker_liveness"


def _loop_action_id(project_id: str, candidate_id: str, suggested_action: str, idempotency_key: str) -> str:
    return f"la-{_stable_id(project_id, candidate_id, suggested_action, idempotency_key)}"


def _stable_id(*parts: object) -> str:
    raw = ":".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]

