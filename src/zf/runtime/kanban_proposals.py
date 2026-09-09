"""Durable pending-proposal projection and exact execution gate.

New proposals use the surface-neutral ``operator.action.*`` events. Historical
``kanban.agent.*`` events remain readable so pending approvals survive an
upgrade without migration.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable

from zf.core.events import ZfEvent
from zf.core.security.redaction import redact_obj

PROPOSAL_EVENT = "operator.action.proposed"
PROPOSAL_RESOLVED_EVENT = "operator.action.resolved"
LEGACY_PROPOSAL_EVENT = "kanban.agent.action.proposed"
LEGACY_PROPOSAL_RESOLVED_EVENT = "kanban.agent.proposal.resolved"
PROPOSAL_EVENT_TYPES = frozenset({PROPOSAL_EVENT, LEGACY_PROPOSAL_EVENT})
PROPOSAL_RESOLVED_EVENT_TYPES = frozenset({
    PROPOSAL_RESOLVED_EVENT,
    LEGACY_PROPOSAL_RESOLVED_EVENT,
})
_PROPOSAL_TRANSPORT_KEYS = frozenset({
    "actor",
    "authorization_ref",
    "causation_id",
    "conversation_id",
    "idempotency_key",
    "origin",
    "project_id",
    "proposal_event_id",
    "run_id",
    "source",
    "surface",
    "thread_id",
})


def canonical_proposal_action(action: str) -> str:
    value = str(action or "").strip()
    if value == "task-workflow-start":
        return "workflow-start"
    return value


def proposal_semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip request-envelope fields that do not change proposed semantics."""
    return {
        str(key): value
        for key, value in payload.items()
        if str(key) not in _PROPOSAL_TRANSPORT_KEYS
    }


def proposal_payload_digest(action: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {
            "action": canonical_proposal_action(action),
            "payload": proposal_semantic_payload(payload),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _proposal_lineage_identity(payload: dict[str, Any]) -> str:
    """Return the durable source identity when a proposal is source-bound.

    A title is only an old compatibility fallback for raw, unthreaded Task
    creation. Channel/PRD handoffs carry a source ref or digest and must never
    be collapsed merely because an unrelated historical Task has the same
    title.
    """
    candidates: list[dict[str, Any]] = [payload]
    for key in ("contract", "source_artifact", "channel_authority"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    contract = payload.get("contract")
    if isinstance(contract, dict):
        evidence_contract = contract.get("evidence_contract")
        if isinstance(evidence_contract, dict):
            candidates.append(evidence_contract)
    for candidate in candidates:
        for key in ("channel_prd_digest", "source_digest", "digest"):
            value = str(candidate.get(key) or "").strip()
            if value:
                return f"digest:{value}"
        for key in ("channel_prd_ref", "source_ref", "ref"):
            value = str(candidate.get(key) or "").strip()
            if value:
                return f"ref:{value}"
    return ""


def pending_kanban_proposals(events: Iterable[ZfEvent]) -> list[dict[str, Any]]:
    event_list = list(events)
    pending: dict[str, dict[str, Any]] = {}
    event_to_proposal: dict[str, str] = {}
    resolved: set[str] = set()
    resolved_proposals: set[str] = set()
    superseded_proposals: set[str] = set()
    # Exact event/proposal references are primary. Title fallback remains only
    # for historical raw API creates with no durable source identity.
    created_title_fallbacks: set[str] = set()
    created_source_lineages: set[tuple[str, str]] = set()
    for event in event_list:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in PROPOSAL_EVENT_TYPES:
            proposal = (
                payload.get("proposal")
                if isinstance(payload.get("proposal"), dict)
                else {}
            )
            if not proposal:
                continue
            action_payload = proposal.get("payload") if isinstance(proposal.get("payload"), dict) else {}
            proposal_id = str(proposal.get("proposal_id") or event.id)
            event_to_proposal[event.id] = proposal_id
            supersedes = str(proposal.get("supersedes") or "")
            if supersedes and supersedes != proposal_id:
                superseded_proposals.add(supersedes)
            record = {
                "proposal_event_id": event.id,
                "proposal_id": proposal_id,
                "proposal_digest": str(proposal.get("proposal_digest") or ""),
                "revision": _revision(proposal.get("revision")),
                "expires_at": str(proposal.get("expires_at") or ""),
                "source": str(payload.get("source") or event.actor or ""),
                "ts": event.ts,
                "action": str(proposal.get("action") or ""),
                "requested_action": str(proposal.get("requested_action") or ""),
                "reason": str(proposal.get("reason") or ""),
                "valid": bool(proposal.get("valid")),
                "validation_error": str(proposal.get("validation_error") or ""),
                "title": str(action_payload.get("title") or ""),
                "payload": action_payload,
                "turn_id": str(payload.get("turn_id") or ""),
                "conversation_id": str(payload.get("conversation_id") or ""),
                "thread_key": str(payload.get("thread_key") or ""),
                "refs": payload.get("refs") if isinstance(payload.get("refs"), dict) else {},
            }
            prior = pending.get(proposal_id)
            if prior is None or (
                record["revision"],
                record["ts"],
            ) >= (
                _revision(prior.get("revision")),
                str(prior.get("ts") or ""),
            ):
                prior_ids = (
                    list(prior.get("proposal_event_ids") or [])
                    if prior is not None
                    else []
                )
                record["proposal_event_ids"] = [
                    *prior_ids,
                    event.id,
                ]
                pending[proposal_id] = record
            elif prior is not None:
                prior.setdefault("proposal_event_ids", []).append(event.id)
        elif event.type in PROPOSAL_RESOLVED_EVENT_TYPES:
            event_id = str(payload.get("proposal_event_id") or "")
            resolved.add(event_id)
            proposal_id = str(payload.get("proposal_id") or event_to_proposal.get(event_id) or "")
            if proposal_id:
                resolved_proposals.add(proposal_id)
        elif event.type == "task.created":
            request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
            threaded = str(request.get("proposal_event_id") or "")
            if threaded:
                resolved.add(threaded)
                proposal_id = event_to_proposal.get(threaded)
                if proposal_id:
                    resolved_proposals.add(proposal_id)
            task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
            title = str(request.get("title") or task.get("title") or "").strip()
            lineage = _proposal_lineage_identity(request) or _proposal_lineage_identity(task)
            if title and lineage:
                created_source_lineages.add((title, lineage))
            elif title:
                created_title_fallbacks.add(title)
    out = []
    for proposal_id, record in pending.items():
        if (
            proposal_id in resolved_proposals
            or proposal_id in superseded_proposals
        ):
            continue
        if any(
            event_id in resolved
            for event_id in record.get("proposal_event_ids") or []
        ):
            continue
        if _expired(str(record.get("expires_at") or "")):
            continue
        if record["action"] in {"create-task", "idea-to-product"} and record["title"]:
            title = record["title"].strip()
            lineage = _proposal_lineage_identity(record["payload"])
            if lineage and (title, lineage) in created_source_lineages:
                continue
            if not lineage and title in created_title_fallbacks:
                continue
        out.append(redact_obj(record))
    return sorted(
        out,
        key=lambda item: str(item.get("ts") or ""),
        reverse=True,
    )


def proposal_execution_gate(
    events: Iterable[ZfEvent],
    *,
    proposal_event_id: str,
    action: str,
    execution_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate exact proposal identity and dedupe execution across surfaces."""
    event_list = list(events)
    source = next(
        (
            event
            for event in event_list
            if event.id == proposal_event_id
            and event.type in PROPOSAL_EVENT_TYPES
        ),
        None,
    )
    if source is None:
        return {"ok": False, "status": "proposal_not_found"}
    source_payload = source.payload if isinstance(source.payload, dict) else {}
    proposal = (
        source_payload.get("proposal")
        if isinstance(source_payload.get("proposal"), dict)
        else {}
    )
    proposal_action = str(proposal.get("action") or "")
    if (
        action != "kanban-proposal-dismiss"
        and canonical_proposal_action(proposal_action)
        != canonical_proposal_action(action)
    ):
        return {"ok": False, "status": "proposal_action_mismatch"}
    if not bool(proposal.get("valid")) and action != "kanban-proposal-dismiss":
        return {"ok": False, "status": "proposal_invalid"}
    if _expired(str(proposal.get("expires_at") or "")):
        return {"ok": False, "status": "proposal_expired"}
    proposal_id = str(proposal.get("proposal_id") or source.id)
    revision = _revision(proposal.get("revision"))
    latest_revision = revision
    for event in event_list:
        if event.type not in PROPOSAL_EVENT_TYPES:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        candidate = payload.get("proposal") if isinstance(payload.get("proposal"), dict) else {}
        if (
            str(candidate.get("supersedes") or "") == proposal_id
            and event.id != source.id
        ):
            return {
                "ok": False,
                "status": "proposal_superseded",
                "proposal_id": proposal_id,
                "revision": revision,
                "superseded_by": str(candidate.get("proposal_id") or event.id),
            }
        if str(candidate.get("proposal_id") or event.id) == proposal_id:
            latest_revision = max(
                latest_revision,
                _revision(candidate.get("revision")),
            )
    if revision < latest_revision:
        return {
            "ok": False,
            "status": "proposal_superseded",
            "proposal_id": proposal_id,
            "revision": revision,
            "latest_revision": latest_revision,
        }

    proposed_payload = (
        proposal.get("payload")
        if isinstance(proposal.get("payload"), dict)
        else {}
    )
    proposal_task_id = str(
        source.task_id or proposed_payload.get("task_id") or ""
    ).strip()
    if (
        action != "kanban-proposal-dismiss"
        and execution_payload is not None
        and proposal_payload_digest(action, proposed_payload)
        != proposal_payload_digest(action, execution_payload)
    ):
        return {
            "ok": False,
            "status": "proposal_payload_mismatch",
            "proposal_id": proposal_id,
            "revision": revision,
        }

    for event in event_list:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in PROPOSAL_RESOLVED_EVENT_TYPES and (
            str(payload.get("proposal_event_id") or "") == proposal_event_id
            or str(payload.get("proposal_id") or "") == proposal_id
        ):
            return {
                "ok": True,
                "status": "already_resolved",
                "proposal_id": proposal_id,
                "proposal_digest": str(proposal.get("proposal_digest") or ""),
                "revision": revision,
                "resolution_event_id": event.id,
                "task_id": str(event.task_id or proposal_task_id),
            }
        if event.type == "task.created":
            request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
            if str(request.get("proposal_event_id") or "") == proposal_event_id:
                task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
                return {
                    "ok": True,
                    "status": "already_resolved",
                    "proposal_id": proposal_id,
                    "proposal_digest": str(proposal.get("proposal_digest") or ""),
                    "revision": revision,
                    "resolution_event_id": event.id,
                    "task_id": str(task.get("id") or event.task_id or ""),
                }
    return {
        "ok": True,
        "status": "ready",
        "proposal_id": proposal_id,
        "proposal_digest": str(proposal.get("proposal_digest") or ""),
        "revision": revision,
        "task_id": proposal_task_id,
        "proposal_event_type": source.type,
        "proposal_context": {
            key: source_payload[key]
            for key in (
                "conversation_id",
                "project_id",
                "thread_key",
                "turn_id",
            )
            if source_payload.get(key) not in (None, "")
        },
    }


def _expired(value: str) -> bool:
    if not value:
        return False
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def _revision(value: object) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1
