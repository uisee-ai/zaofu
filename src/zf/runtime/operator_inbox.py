"""Operator inbox projection.

This is a read-only view over ``events.jsonl``. Controlled actions still own
approve/reject mutations.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.operator_plan_preview import (
    PLAN_APPROVAL_REQUESTED,
    PLAN_APPROVED,
    PLAN_REJECTED,
    plan_preview_available,
)
from zf.runtime.operator_inbox_read import inbox_item_version, put_inbox_item, record_inbox_read

GENERIC_APPROVAL_REQUESTED = "approval.requested"
GENERIC_APPROVAL_RESOLVED = "approval.resolved"
GENERIC_APPROVAL_EXPIRED = "approval.expired"
GENERIC_APPROVAL_POLICY_REJECTED = "approval.rejected_by_policy"
HUMAN_ESCALATION_REQUESTED = "human.escalate"
HUMAN_ESCALATION_SENT = "human.escalation.sent"
HUMAN_ESCALATION_ACKNOWLEDGED = "human.escalation.acknowledged"
RUN_MANAGER_HUMAN_DECISION_APPLIED = "run.manager.human_decision.applied"
RUN_MANAGER_HUMAN_DECISION_REJECTED = "run.manager.human_decision.rejected"
INBOX_ITEM_READ = "inbox.item.read"
INBOX_ALL_READ = "inbox.all.read"
OWNER_VISIBLE_MESSAGE_REQUESTED = "owner.visible_message.requested"

OPERATOR_INBOX_SCHEMA_VERSION = "operator-inbox.v3"
_ACTION_REQUIRED_KINDS = {"plan_approval", "approval"}


def _project_operator_requests(
    state_dir: Path,
    events: Iterable[Any],
    *,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Project approval and recovery requests from event truth.

    The inbox intentionally exposes actions as names only. Executing those
    names remains the Web controlled-action path's responsibility.
    """

    events = list(events)
    typed_events = [
        ZfEvent.from_dict(event) if isinstance(event, dict) else event
        for event in events
    ]
    events_by_id = {
        _event_id(event): event for event in events if _event_id(event)
    }
    items: dict[str, dict[str, Any]] = {}
    attention_aliases: dict[str, str] = {}
    read_versions: dict[str, set[str]] = {}
    for event in events:
        etype = _etype(event)
        payload = _payload(event)
        event_id = _event_id(event)
        ts = _event_ts(event)

        if etype in {INBOX_ITEM_READ, INBOX_ALL_READ}:
            record_inbox_read(etype, payload, items, read_versions)
            continue

        from zf.runtime.operator_workflow_approvals import project_workflow_approval_event

        if project_workflow_approval_event(state_dir, items, event):
            continue

        if etype == OWNER_VISIBLE_MESSAGE_REQUESTED:
            # Older owner alerts can be the only evidence of an outstanding
            # human request. Keep them visible for reconciliation, without
            # inventing an approval action or importing terminal reports.
            if (payload.get("human_action_required") is True
                and payload.get("message_kind") != "run_terminal_delivery"
                and payload.get("delivery_class") != "run_terminal"):
                key = _attention_item_id(payload, event_id, attention_aliases)
                if key not in items:
                    put_inbox_item(items, key, _owner_attention_item(event=event, payload=payload, key=key))
            continue

        if etype == PLAN_APPROVAL_REQUESTED:
            plan_id = str(payload.get("plan_id") or "")
            if not plan_id:
                continue
            put_inbox_item(items, _plan_item_id(plan_id), _plan_approval_item(
                state_dir,
                event=event,
                payload=payload,
                project_root=project_root,
                status="pending",
                resolved_event_id="",
                resolved_ts="",
                reject_reason="",
            ))
            continue

        if etype in {PLAN_APPROVED, PLAN_REJECTED}:
            plan_id = str(payload.get("plan_id") or "")
            if not plan_id:
                continue
            key = _plan_item_id(plan_id)
            existing = items.get(key)
            if existing is None:
                # Automatic and request-less decisions are not human Inbox
                # history; the source events remain available elsewhere.
                continue
            if etype == PLAN_APPROVED and payload.get("semantic_control"):
                # Agent adoption is not the Owner approval required by this request.
                continue
            if etype == PLAN_APPROVED and payload.get("auto"):
                items.pop(key, None)
                continue
            existing["status"] = "approved" if etype == PLAN_APPROVED else "rejected"
            existing["resolved_event_id"] = event_id
            existing["resolved_ts"] = ts
            if etype == PLAN_REJECTED:
                existing["reject_reason"] = str(payload.get("reason") or "")
            continue

        if etype == GENERIC_APPROVAL_REQUESTED:
            if payload.get("owner_route") == "owner_visible_delivery":
                # The original human request, if any, is projected above.
                # A failed delivery route is not itself an approval.
                continue
            approval_ref = _approval_ref(payload, event_id)
            payload = _owner_preview_payload(
                payload,
                events_by_id=events_by_id,
                state_dir=state_dir,
                project_root=project_root,
            )
            put_inbox_item(items, _generic_item_id(approval_ref), _generic_approval_item(
                event=event,
                payload=payload,
                approval_ref=approval_ref,
                status="pending",
                state_dir=state_dir,
                project_root=project_root,
            ))
            continue

        if etype in {
            GENERIC_APPROVAL_RESOLVED,
            GENERIC_APPROVAL_EXPIRED,
            GENERIC_APPROVAL_POLICY_REJECTED,
        }:
            approval_ref = _approval_ref(payload, "")
            if not approval_ref:
                continue
            key = _generic_item_id(approval_ref)
            existing = items.get(key)
            if existing is None:
                continue
            existing["status"] = _generic_resolution_status(etype, payload)
            existing["resolved_event_id"] = event_id
            existing["resolved_ts"] = ts
            existing["resolution"] = str(payload.get("resolution") or payload.get("reason") or "")
            continue

        if etype in {HUMAN_ESCALATION_REQUESTED, HUMAN_ESCALATION_SENT}:
            token = _human_decision_token(payload, event_id)
            if not token:
                continue
            key = _human_item_id(token)
            if key in items and items[key].get("status") != "pending":
                continue
            put_inbox_item(items, key, _human_decision_item(
                event=event,
                payload=payload,
                decision_token=token,
                status="pending",
            ))
            continue

        if etype in {
            HUMAN_ESCALATION_ACKNOWLEDGED,
            RUN_MANAGER_HUMAN_DECISION_APPLIED,
            RUN_MANAGER_HUMAN_DECISION_REJECTED,
        }:
            token = _human_decision_token(payload, event_id)
            if not token:
                continue
            key = _human_item_id(token)
            existing = items.get(key)
            if existing is None and etype == HUMAN_ESCALATION_ACKNOWLEDGED:
                continue
            if existing is None:
                existing = _human_decision_item(
                    event=event,
                    payload=payload,
                    decision_token=token,
                    status="resolved",
                )
                items[key] = existing
            existing["status"] = _human_resolution_status(etype, payload)
            existing["resolved_event_id"] = event_id
            existing["resolved_ts"] = ts
            existing["resolution"] = str(
                payload.get("reason") or payload.get("controlled_action_status")
                or payload.get("decision") or payload.get("next_route") or ""
            )
            continue

        if etype.startswith("runtime.attention."):
            attention_id = str(payload.get("attention_id") or payload.get("id") or event_id)
            key = _attention_item_id(payload, event_id, attention_aliases)
            terminal_status = _attention_terminal_status(etype)
            if terminal_status:
                existing = items.get(key)
                if existing is not None:
                    existing["status"] = terminal_status
                    existing["resolved_event_id"] = event_id
                    existing["resolved_ts"] = ts
                    existing["resolution"] = str(payload.get("reason") or terminal_status)
                elif terminal_status == "acknowledged":
                    continue
            if etype not in {"runtime.attention.needed", "runtime.attention.unacknowledged", "runtime.attention.escalated"}:
                continue
            # Runtime attention remains in supervisor/dashboard projections.
            continue

    if any(item.get("kind") == "human_decision" and item.get("status") == "pending" for item in items.values()):
        from zf.runtime.run_manager import (
            RUN_COMPLETED, _PROGRESS_SUCCESS_EVENTS, _human_escalations_by_token, _pending_human_decisions,
        )

        current_decisions = {row["decision_token"]: row for row in _pending_human_decisions(typed_events)}
        known_tokens = _human_escalations_by_token(typed_events)
        event_positions = {event.id: index for index, event in enumerate(typed_events)}
        for item in items.values():
            token = str(item.get("decision_token") or "")
            current = current_decisions.get(token)
            if current and current["review_only"] and item.get("status") == "pending":
                item["summary"] = current["question"] or item["summary"]
                item["actions"] = [option for option in item["actions"]
                                   if option["action"] != "human-decision-approve-controlled-action"]
            if (item.get("kind") == "human_decision" and item.get("status") == "pending"
                and token in known_tokens and token not in current_decisions):
                source = known_tokens[token]
                source_event = events_by_id.get(str(source.get("event_id") or ""))
                run_id = str(source.get("run_id") or source.get("workflow_run_id")
                             or _correlation_id(source_event) or "")
                source_index = event_positions.get(str(source.get("event_id") or ""), -1)
                same_run_progress = bool(run_id and source_index >= 0 and any(
                    event.type in _PROGRESS_SUCCESS_EVENTS | {RUN_COMPLETED}
                    and run_id == str(_payload(event).get("run_id") or _payload(event).get("workflow_run_id")
                                      or event.correlation_id or "")
                    for event in typed_events[source_index + 1:]
                ))
                if same_run_progress:
                    item["status"] = "superseded"
                    item["resolution"] = "Run Manager 已确认该决定不再待处理"
                else:
                    item["verification_required"] = True
                    item["actions"] = []

    return sorted(
        (_decorate_item(item, read_versions=read_versions) for item in items.values()),
        key=lambda item: (item.get("status") != "pending", item.get("created_ts") or ""),
    )


def _is_review_request(item: dict[str, Any]) -> bool:
    if item.get("kind") == "plan_approval":
        return True
    return item.get("kind") == "approval" and item.get("reviewable") is True


def build_runtime_requests(
    state_dir: Path, events: Iterable[Any], *, project_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Keep recovery and legacy requests discoverable at their operations source.

    This does not resolve requests or grant authorization. Existing controlled
    actions still validate current source contracts when the operator submits.
    """
    return [item for item in _project_operator_requests(state_dir, events, project_root=project_root)
            if not _is_review_request(item)]


def build_operator_inbox(
    state_dir: Path, events: Iterable[Any], *, project_root: Path | None = None,
) -> dict[str, Any]:
    """Project human review/authorization only; source state is never mutated."""
    ordered = [item for item in _project_operator_requests(state_dir, events, project_root=project_root)
               if _is_review_request(item)]
    pending = [item for item in ordered if item.get("status") == "pending"]
    return {
        "schema_version": OPERATOR_INBOX_SCHEMA_VERSION,
        "is_derived_projection": True,
        "summary": {
            "total": len(ordered), "pending": len(pending),
            "action_required_pending": len(pending),
            "plan_approvals": sum(item.get("kind") == "plan_approval" for item in ordered),
            "approvals": sum(item.get("kind") == "approval" for item in ordered),
            "unread": sum(bool(item.get("unread")) for item in pending),
            # Retain zero-valued v2 counters for old clients, never old items.
            **dict.fromkeys(("noise_pending", "notification_pending", "attention", "human_decisions",
                            "decisions", "decisions_pending", "channel_decisions", "kanban_questions",
                            "run_deliveries", "suppressed_acknowledged"), 0),
        },
        "items": ordered, "pending": pending, "views": _build_views(ordered),
        "policy": {
            "truth_source": "events.jsonl", "mutation_path": "controlled-action",
            "agent_can_propose_plan_approve": False, "agent_can_propose_plan_reject": True,
            "admitted_kinds": ["plan_approval", "approval"],
        },
    }


def _decorate_item(
    item: dict[str, Any],
    *,
    read_versions: dict[str, set[str]],
) -> dict[str, Any]:
    decorated = dict(item)
    status = str(decorated.get("status") or "")
    kind = str(decorated.get("kind") or "")

    decorated.setdefault("dedupe_count", 1)
    decorated.setdefault("first_seen_at", str(decorated.get("created_ts") or ""))
    decorated.setdefault("last_seen_at", str(decorated.get("created_ts") or ""))
    decorated.setdefault("latest_event_id", str(decorated.get("created_event_id") or ""))
    decorated.setdefault("source_role", _source_role_for_item(decorated))
    decorated.setdefault("owner_route", _owner_route_for_item(decorated))
    decorated.setdefault("group_key", _group_key_for_item(decorated))
    version = inbox_item_version(decorated)
    decorated["read_version"] = version
    decorated["unread"] = (
        status == "pending"
        and version not in read_versions.get(str(decorated.get("id") or ""), set())
    )

    if status != "pending":
        decorated["actions"] = []
        decorated["category"] = "resolved"
        decorated["actionability"] = "resolved"
        return decorated

    if kind in _ACTION_REQUIRED_KINDS:
        decorated["category"] = "action_required"
        decorated["actionability"] = "human_required"
        return decorated

    if kind == "runtime_attention":
        if decorated.get("source_role") in {"supervisor", "autoresearch", "run_manager", "orchestrator"}:
            decorated["category"] = "automation_diagnostic"
        else:
            decorated["category"] = "runtime_attention"
        decorated["actionability"] = "automation_owned"
        return decorated

    decorated["category"] = "notification"
    decorated["actionability"] = "informational"
    return decorated


def _build_views(items: list[dict[str, Any]]) -> dict[str, Any]:
    view_defs = {
        "action_required": lambda item: item.get("status") == "pending" and item.get("actionability") == "human_required",
        "decisions": lambda item: item.get("status") == "pending" and item.get("kind") in _ACTION_REQUIRED_KINDS,
        "runtime_attention": lambda item: item.get("status") == "pending" and item.get("category") == "runtime_attention",
        "automation": lambda item: item.get("status") == "pending" and item.get("category") == "automation_diagnostic",
        "notification": lambda item: item.get("status") == "pending" and item.get("category") == "notification",
        "resolved": lambda item: item.get("status") != "pending" or item.get("category") == "resolved",
        "all": lambda item: True,
    }
    views: dict[str, Any] = {}
    for name, predicate in view_defs.items():
        ids = [str(item.get("id") or "") for item in items if predicate(item)]
        views[name] = {"count": len(ids), "ids": ids}
    return views


def _group_key_for_item(item: dict[str, Any]) -> str:
    fingerprint = str(item.get("fingerprint") or "").strip()
    if fingerprint:
        return f"fingerprint:{fingerprint}"
    for prefix, key in (
        ("plan", "plan_id"),
        ("human", "decision_token"),
        ("approval", "approval_ref"),
        ("attention", "attention_id"),
        ("question", "question_id"),
        ("kanban", "request_id"),
        ("event", "created_event_id"),
    ):
        value = str(item.get(key) or "").strip()
        if value:
            return f"{prefix}:{value}"
    title = str(item.get("title") or "").strip().lower().replace(" ", "-")
    return f"{item.get('kind') or 'item'}:{title or item.get('id') or 'unknown'}"


def _owner_route_for_item(item: dict[str, Any]) -> str:
    explicit = str(item.get("owner_route") or "").strip()
    if explicit:
        return explicit
    if item.get("kind") in _ACTION_REQUIRED_KINDS:
        return "human"
    source_role = str(item.get("source_role") or "")
    if source_role in {"run_manager", "supervisor", "autoresearch", "orchestrator"}:
        return source_role
    return "none"


def _source_role_for_item(item: dict[str, Any]) -> str:
    explicit = str(item.get("source_role") or "").strip()
    return explicit or "unknown"


def _source_role(event: Any, payload: dict[str, Any]) -> str:
    explicit = str(
        payload.get("source_role")
        or payload.get("owner_role")
        or payload.get("owner")
        or payload.get("source")
        or ""
    ).strip().lower().replace("-", "_")
    if explicit in {"run_manager", "supervisor", "autoresearch", "orchestrator", "worker", "web", "operator"}:
        return explicit
    etype = _etype(event).replace("-", "_")
    actor = _actor(event).replace("-", "_")
    combined = f"{actor} {etype}"
    if "run_manager" in combined or "run.manager" in etype or "run-manager" in actor:
        return "run_manager"
    if "supervisor" in combined:
        return "supervisor"
    if "autoresearch" in combined:
        return "autoresearch"
    if "orchestrator" in combined:
        return "orchestrator"
    if "worker" in combined or "lane" in actor:
        return "worker"
    if "web" in combined:
        return "web"
    if "operator" in combined:
        return "operator"
    return "unknown"


def _actor(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("actor") or "")
    return str(getattr(event, "actor", "") or "")


def _plan_approval_item(
    state_dir: Path,
    *,
    event: Any,
    payload: dict[str, Any],
    project_root: Path | None,
    status: str,
    resolved_event_id: str,
    resolved_ts: str,
    reject_reason: str,
) -> dict[str, Any]:
    plan_id = str(payload.get("plan_id") or "")
    digest_ref = str(payload.get("digest_ref") or "")
    task_map_ref = str(payload.get("task_map_ref") or "")
    return {
        "id": _plan_item_id(plan_id),
        "kind": "plan_approval",
        "status": status,
        "title": "Plan Ready",
        "summary": _plan_summary(payload),
        "created_event_id": _event_id(event),
        "created_ts": _event_ts(event),
        "resolved_event_id": resolved_event_id,
        "resolved_ts": resolved_ts,
        "source_role": _source_role(event, payload),
        "source_actor": _actor(event),
        "approval_ref": f"plan:{plan_id}",
        "plan_id": plan_id,
        "stage_id": str(payload.get("stage_id") or ""),
        "trace_id": str(payload.get("trace_id") or _correlation_id(event) or ""),
        "pdd_id": str(payload.get("pdd_id") or ""),
        "task_count": _int_or_none(payload.get("task_count")),
        "refs": {
            "digest_ref": digest_ref,
            "task_map_ref": task_map_ref,
        },
        "preview": {
            "available": plan_preview_available(
                state_dir,
                project_root=project_root,
                refs=[digest_ref, task_map_ref],
            ),
            "api_path": f"/plans/{plan_id}/preview",
            "fullscreen": True,
            "scroll": True,
        },
        "actions": [
            {"action": "plan-approve", "label": "Approve", "requires_token": True},
            {"action": "plan-reject", "label": "Reject", "requires_token": True, "requires_reason": True},
            {"action": "chat-orchestrator", "label": "Repair Chat", "requires_token": True},
        ],
        "reject_reason": reject_reason,
        "policy": {
            "agent_can_propose_plan_approve": False,
            "repair_owner": "orchestrator",
        },
    }


def _generic_approval_item(
    *,
    event: Any,
    payload: dict[str, Any],
    approval_ref: str,
    status: str,
    state_dir: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    item = {
        "id": _generic_item_id(approval_ref),
        "kind": "approval",
        "status": status,
        "title": str(payload.get("title") or "Approval requested"),
        "summary": str(payload.get("summary") or payload.get("reason") or approval_ref),
        "created_event_id": _event_id(event),
        "created_ts": _event_ts(event),
        "resolved_event_id": "",
        "resolved_ts": "",
        "source_role": _source_role(event, payload),
        "source_actor": _actor(event),
        "approval_ref": approval_ref,
        "actions": [],
    }
    if (payload.get("owner_route") == "plan_synth_owner_decision"
        and payload.get("proposal_ref") and payload.get("eval_ref")):
        item["actions"] = [
            {"action": action, "label": label, "requires_token": True}
            for key, action, label in (
                ("approve_action", "replan-approve", "Approve"),
                ("reject_action", "replan-reject", "Reject"),
            ) if payload.get(key) == action
        ]
    item["reviewable"] = bool(item["actions"])
    if not item["actions"]:
        item.update({"verification_required": True, "deep_link": "?page=runtime", "deep_link_label": "Open runtime"})
    for key in (
        "checkpoint_id",
        "workflow_run_id",
        "fanout_id",
        "source_event_id",
        "proposal_ref",
        "eval_ref",
        "candidate_task_map_ref",
    ):
        value = str(payload.get(key) or "")
        if value:
            item[key] = value
    if str(payload.get("owner_route") or "") == "plan_synth_owner_decision":
        plan_id = str(payload.get("plan_id") or payload.get("source_event_id") or "")
        plan_ref = str(
            payload.get("plan_artifact_ref") or payload.get("plan_ref") or ""
        )
        task_map_ref = str(
            payload.get("task_map_ref") or payload.get("candidate_task_map_ref") or ""
        )
        item.update({
            "plan_id": plan_id,
            "stage_id": str(payload.get("stage_id") or ""),
            "trace_id": str(payload.get("trace_id") or payload.get("workflow_run_id") or ""),
            "pdd_id": str(payload.get("pdd_id") or payload.get("feature_id") or ""),
            "task_count": _int_or_none(payload.get("task_count")),
            "owner_route": str(payload.get("owner_route") or ""),
            "refs": {
                "plan_artifact_ref": plan_ref,
                "plan_artifact_digest": str(payload.get("plan_artifact_digest") or ""),
                "task_map_ref": task_map_ref,
                "source_index_ref": str(payload.get("source_index_ref") or ""),
                "requirement_spec_ref": str(payload.get("requirement_spec_ref") or ""),
                "plan_preview_bundle_ref": str(payload.get("plan_preview_bundle_ref") or ""),
                "plan_preview_bundle_digest": str(payload.get("plan_preview_bundle_digest") or ""),
            },
            "content_profile": str(payload.get("plan_content_profile") or "unknown"),
            "preview": {
                "available": bool(
                    state_dir is not None
                    and project_root is not None
                    and plan_preview_available(
                        state_dir,
                        project_root=project_root,
                        refs=[plan_ref, task_map_ref],
                    )
                ),
                "api_path": f"/plans/{plan_id}/preview" if plan_id else "",
                "fullscreen": True,
                "scroll": True,
            },
        })
    return item


def _owner_preview_payload(
    payload: dict[str, Any],
    *,
    events_by_id: dict[str, Any],
    state_dir: Path,
    project_root: Path | None,
) -> dict[str, Any]:
    if str(payload.get("owner_route") or "") != "plan_synth_owner_decision":
        return payload
    source_id = str(payload.get("source_event_id") or "").strip()
    source = events_by_id.get(source_id)
    source_payload = (
        source.payload if source is not None and isinstance(source.payload, dict) else {}
    )
    if not source_payload:
        return payload
    if payload.get("plan_artifact_ref") and payload.get("task_map_ref"):
        return payload
    from zf.runtime.plan_synth_owner_checkpoint import plan_preview_refs

    refs = plan_preview_refs(
        source_payload,
        state_dir=state_dir,
        project_root=project_root,
    )
    return {
        **payload,
        **{
            key: value for key, value in refs.items()
            if value and not payload.get(key)
        },
    }


def _owner_attention_item(
    *,
    event: Any,
    payload: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    return {
        "id": key,
        "kind": "owner_attention",
        "status": "pending",
        "title": str(payload.get("title") or "Owner request needs review"),
        "summary": str(payload.get("summary") or "Check the original owner request"),
        "created_event_id": _event_id(event),
        "created_ts": _event_ts(event),
        "resolved_event_id": "",
        "resolved_ts": "",
        "source_role": _source_role(event, payload),
        "source_actor": _actor(event),
        "run_id": str(payload.get("run_id") or ""),
        "attention_id": str(payload.get("attention_id") or ""),
        "fingerprint": str(payload.get("fingerprint") or ""),
        "verification_required": True,
        "deep_link": "?page=runtime",
        "deep_link_label": "Open runtime",
        "actions": [],
    }


def _attention_terminal_status(etype: str) -> str:
    suffix = etype.rsplit(".", 1)[-1]
    if suffix in {"acknowledged", "resolved", "dismissed", "cleared", "snoozed"}:
        return suffix
    return ""


def _attention_item_id(
    payload: dict[str, Any],
    event_id: str,
    aliases: dict[str, str],
) -> str:
    keys = _attention_match_keys(payload, event_id)
    for key in keys:
        existing = aliases.get(key)
        if existing:
            for alias in keys:
                aliases[alias] = existing
            return existing
    primary = keys[0] if keys else f"attention:{event_id}"
    for key in keys:
        aliases[key] = primary
    return primary


def _attention_match_keys(payload: dict[str, Any], event_id: str) -> list[str]:
    keys: list[str] = []
    attention_id = str(payload.get("attention_id") or payload.get("id") or "").strip()
    fingerprint = str(payload.get("fingerprint") or "").strip()
    source_event_id = str(payload.get("source_event_id") or "").strip()
    if attention_id:
        keys.append(f"attention:{attention_id}")
    if fingerprint:
        keys.append(f"attention:fingerprint:{fingerprint}")
    if source_event_id:
        keys.append(f"attention:source:{source_event_id}")
    if not keys and event_id:
        keys.append(f"attention:{event_id}")
    return keys


def _human_decision_item(
    *,
    event: Any,
    payload: dict[str, Any],
    decision_token: str,
    status: str,
) -> dict[str, Any]:
    explicit_contract = any(
        str(payload.get(key) or "").strip()
        for key in ("checkpoint_id", "approval_command")
    )
    return {
        "id": _human_item_id(decision_token),
        "kind": "human_decision",
        "status": status,
        "title": "Run Manager Decision",
        "summary": str(payload.get("reason") or payload.get("question") or "Run Manager needs an operator decision"),
        "created_event_id": _event_id(event),
        "created_ts": _event_ts(event),
        "resolved_event_id": "",
        "resolved_ts": "",
        "source_role": _source_role(event, payload),
        "source_actor": _actor(event),
        "decision_token": decision_token,
        "approval_ref": f"human:{decision_token}",
        "checkpoint_id": str(payload.get("checkpoint_id") or ""),
        "fingerprint": str(payload.get("fingerprint") or ""),
        "run_id": str(payload.get("run_id") or payload.get("workflow_run_id") or ""),
        "verification_required": not explicit_contract,
        "deep_link": "?page=runtime",
        "deep_link_label": "Open runtime",
        "actions": [
            {"action": "human-decision-approve-controlled-action", "label": "Approve", "requires_token": True},
            {"action": "human-decision-request-autoresearch", "label": "Diagnose", "requires_token": True},
            {"action": "human-decision-dismiss", "label": "Dismiss", "requires_token": True},
            {"action": "human-decision-safe-halt", "label": "Halt", "requires_token": True},
        ] if explicit_contract else [],
        "policy": {
            "mutation_path": "human.escalation.acknowledged -> run_manager_tick",
            "agent_can_approve": False,
        },
    }


def _plan_summary(payload: dict[str, Any]) -> str:
    stage = str(payload.get("stage_id") or "plan")
    task_count = payload.get("task_count")
    pdd_id = str(payload.get("pdd_id") or "")
    count = f"{task_count} tasks" if task_count not in (None, "") else "task map ready"
    return " / ".join(part for part in (stage, count, pdd_id) if part)


def _generic_resolution_status(etype: str, payload: dict[str, Any]) -> str:
    if etype == GENERIC_APPROVAL_EXPIRED:
        return "expired"
    if etype == GENERIC_APPROVAL_POLICY_REJECTED:
        return "rejected_by_policy"
    return str(payload.get("status") or payload.get("resolution") or "resolved")


def _approval_ref(payload: dict[str, Any], fallback: str) -> str:
    return str(payload.get("approval_ref") or payload.get("approval_id") or fallback)


def _human_decision_token(payload: dict[str, Any], fallback: str) -> str:
    raw = str(
        payload.get("decision_token")
        or payload.get("response_token")
        or payload.get("approval_ref")
        or payload.get("source_message_id")
        or payload.get("escalation_event_id")
        or fallback
    )
    if raw.startswith("human:"):
        raw = raw.removeprefix("human:")
    return raw


def _human_resolution_status(etype: str, payload: dict[str, Any]) -> str:
    if etype == RUN_MANAGER_HUMAN_DECISION_REJECTED:
        return "rejected"
    if etype == RUN_MANAGER_HUMAN_DECISION_APPLIED:
        status = str(payload.get("controlled_action_status") or "")
        if status in {"failed", "blocked", "rejected", "unsupported", "error"}:
            return "failed"
        if payload.get("decision") == "request_autoresearch" and payload.get("autoresearch_requested") is False:
            return "failed"
        return "applied"
    return "processing"


def _plan_item_id(plan_id: str) -> str:
    return f"plan:{plan_id}"


def _generic_item_id(approval_ref: str) -> str:
    return f"approval:{approval_ref}"


def _human_item_id(decision_token: str) -> str:
    return f"human:{decision_token}"


def _etype(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("type") or "")
    return str(getattr(event, "type", "") or "")


def _payload(event: Any) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _event_id(event: Any) -> str:
    if event is None:
        return ""
    if isinstance(event, dict):
        return str(event.get("id") or "")
    return str(getattr(event, "id", "") or "")


def _event_ts(event: Any) -> str:
    if event is None:
        return ""
    if isinstance(event, dict):
        return str(event.get("ts") or "")
    return str(getattr(event, "ts", "") or "")


def _correlation_id(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("correlation_id") or "")
    return str(getattr(event, "correlation_id", "") or "")


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


__all__ = [
    "OPERATOR_INBOX_SCHEMA_VERSION",
    "build_operator_inbox",
]
