"""Lightweight, paginated Channel conversation projection for Web chat."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.channel_contract_artifacts import CONTRIBUTION_SCHEMA_VERSION
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_parsing import structured_reply_display_text
from zf.runtime.channel_sidecar import hydrate_channel_message_text
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


DEFAULT_CONVERSATION_LIMIT = 50
MAX_CONVERSATION_LIMIT = 100
_ACTIVE_RUN_STATUSES = {"pending", "queued", "running", "started", "streaming", "submitted", "waiting_input"}
_FAILED_RUN_STATUSES = {"failed", "rejected", "escalated"}


def empty_channel_conversation(channel_id: str) -> dict[str, Any]:
    public_id = str(channel_id or "ch-zaofu")
    return {
        "schema_version": "channel.conversation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seq": 0,
        "source": "events.jsonl",
        "empty": True,
        "id": public_id,
        "channel_id": public_id,
        "name": "# zaofu" if public_id.lower() in {"zaofu", "ch-zaofu"} else public_id,
        "status": "empty",
        "members": [],
        "threads": [],
        "messages": [],
        "reply_requests": [],
        "provider_runs": [],
        "agent_session_runs": [],
        "workflow_requests": [],
        "discussion_attention": {},
        "open_questions": [],
        "contributions": [],
        "message_count": 0,
        "has_more": False,
        "next_before": "",
        "page": {
            "limit": DEFAULT_CONVERSATION_LIMIT,
            "returned": 0,
            "before": "",
            "has_more": False,
            "next_before": "",
        },
        "diagnostics_summary": {},
    }


def project_channel_conversation(
    state_dir: Path,
    channel_id: str,
    *,
    limit: int = DEFAULT_CONVERSATION_LIMIT,
    before: str = "",
    events: list[ZfEvent] | None = None,
) -> dict[str, Any] | None:
    """Project one bounded chat page without diagnostic-heavy duplicates.

    ``events`` may be a channel-scoped slice from the rebuildable read model.
    The caller keeps the full-ledger fallback when that projection is absent.
    """

    state_dir = Path(state_dir)
    detail = project_channel(
        state_dir,
        channel_id,
        events=events,
        include_linked_events=False,
    )
    if detail is None:
        return None
    messages = [
        item for item in detail.get("messages") or []
        if isinstance(item, dict)
    ]
    page_messages, has_more, next_before = _message_page(
        messages,
        limit=limit,
        before=before,
    )
    page_message_ids = {
        str(item.get("message_id") or "") for item in page_messages
    }
    page_request_ids = {
        str((item.get("refs") or {}).get("request_id") or "")
        for item in page_messages
        if isinstance(item.get("refs"), dict)
    }
    contributions = [
        item for item in detail.get("contributions") or []
        if isinstance(item, dict)
        and (
            str(item.get("message_id") or "") in page_message_ids
            or str(item.get("request_id") or "") in page_request_ids
        )
    ]
    contribution_by_request = {
        str(item.get("request_id") or ""): _conversation_contribution(
            state_dir,
            item,
        )
        for item in contributions
        if str(item.get("request_id") or "")
    }
    public_messages = [
        _conversation_message(
            state_dir,
            message,
            contribution_by_request=contribution_by_request,
        )
        for message in page_messages
    ]
    reply_requests = [
        _compact_reply_request(item)
        for item in detail.get("reply_requests") or []
        if isinstance(item, dict)
        and _request_in_conversation(
            item,
            page_message_ids=page_message_ids,
            page_request_ids=page_request_ids,
        )
    ]
    provider_runs = [
        _compact_provider_run(item)
        for item in detail.get("provider_runs") or []
        if isinstance(item, dict)
        and _provider_run_in_conversation(
            item,
            page_message_ids=page_message_ids,
            page_request_ids=page_request_ids,
        )
    ]
    routes = [
        _compact_mapping(item, text_limit=480)
        for item in detail.get("routes") or []
        if isinstance(item, dict)
        and str(item.get("message_id") or "") in page_message_ids
    ]
    selected_request_ids = {
        str(item.get("request_id") or "") for item in reply_requests
    } | page_request_ids
    public_contributions = [
        value for request_id, value in contribution_by_request.items()
        if value and request_id in selected_request_ids
    ]
    payload = {
        "schema_version": "channel.conversation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seq": detail.get("seq") or detail.get("last_event_seq") or 0,
        "source": "events.jsonl",
        "empty": bool(detail.get("empty")),
        "id": detail.get("id") or detail.get("channel_id") or channel_id,
        "channel_id": detail.get("channel_id") or channel_id,
        "name": detail.get("name") or channel_id,
        "status": detail.get("status") or "active",
        "task_id": detail.get("task_id") or "",
        "created_at": detail.get("created_at") or "",
        "created_by": detail.get("created_by") or "",
        "owner_actor_ref": detail.get("owner_actor_ref") or "",
        "leader_member_id": detail.get("leader_member_id") or "",
        "leader_revision": detail.get("leader_revision") or 0,
        "origin_binding": detail.get("origin_binding") or {},
        "scope": detail.get("scope") or {},
        "members": detail.get("members") or [],
        "member_count": detail.get("member_count") or 0,
        "threads": detail.get("threads") or [],
        "read_state": detail.get("read_state") or [],
        "unread_count": detail.get("unread_count") or 0,
        "pinned_message_ids": detail.get("pinned_message_ids") or [],
        "attention": detail.get("attention") or [],
        "messages": public_messages,
        "message_count": detail.get("message_count") or len(messages),
        "page": {
            "limit": max(1, min(int(limit or DEFAULT_CONVERSATION_LIMIT), MAX_CONVERSATION_LIMIT)),
            "returned": len(public_messages),
            "before": str(before or ""),
            "has_more": has_more,
            "next_before": next_before,
        },
        "has_more": has_more,
        "next_before": next_before,
        "reply_requests": reply_requests,
        "provider_runs": provider_runs,
        "agent_session_runs": [],
        "running_replies": [
            item for item in reply_requests
            if str(item.get("status") or "") in _ACTIVE_RUN_STATUSES
        ],
        "queued_replies": [
            item for item in reply_requests
            if str(item.get("status") or "") == "queued"
        ],
        "pending_reply_count": sum(
            1 for item in detail.get("reply_requests") or []
            if isinstance(item, dict)
            and str(item.get("status") or "") in _ACTIVE_RUN_STATUSES
        ),
        "typing": detail.get("typing") or [],
        "active_typing": detail.get("active_typing") or [],
        "attachments": _selected_rows(detail.get("attachments"), page_message_ids),
        "artifacts": _selected_rows(detail.get("artifacts"), page_message_ids),
        "contributions": public_contributions,
        "discussion": detail.get("discussion") or {},
        "discussions": detail.get("discussions") or {},
        "discussion_attention": detail.get("discussion_attention") or {},
        "open_questions": detail.get("open_questions") or [],
        "question_frontiers": detail.get("question_frontiers") or {},
        "owner_questionnaires": detail.get("owner_questionnaires") or {},
        "question_graph_digests": detail.get("question_graph_digests") or {},
        "consensus": detail.get("consensus") or {},
        "syntheses": _compact_rows(detail.get("syntheses"), limit=8),
        "workflow_requests": _compact_rows(detail.get("workflow_requests"), limit=12),
        "handoffs": _compact_rows(detail.get("handoffs"), limit=12),
        "state_updates": _compact_rows(detail.get("state_updates"), limit=20),
        "result_receipts": _compact_rows(detail.get("result_receipts"), limit=20),
        "mentions_detected": _compact_rows(detail.get("mentions_detected"), limit=30),
        "routes": routes,
        "history_cleared_at": detail.get("history_cleared_at") or "",
        "history_clear_event_id": detail.get("history_clear_event_id") or "",
        "history_clear_reason": detail.get("history_clear_reason") or "",
        "updated_at": detail.get("updated_at") or "",
        "diagnostics_summary": {
            "reply_request_count": len(detail.get("reply_requests") or []),
            "provider_run_count": len(detail.get("provider_runs") or []),
            "agent_session_run_count": len(detail.get("agent_session_runs") or []),
            "context_pack_count": len(detail.get("context_packs") or []),
            "linked_event_count": int(detail.get("linked_event_count") or 0),
        },
    }
    return redact_obj(payload)


def _message_page(
    messages: list[dict[str, Any]],
    *,
    limit: int,
    before: str,
) -> tuple[list[dict[str, Any]], bool, str]:
    safe_limit = max(1, min(int(limit or DEFAULT_CONVERSATION_LIMIT), MAX_CONVERSATION_LIMIT))
    end = len(messages)
    cursor = str(before or "").strip()
    if cursor:
        for index, item in enumerate(messages):
            if cursor in {
                str(item.get("message_id") or ""),
                str(item.get("event_id") or ""),
            }:
                end = index
                break
    start = max(0, end - safe_limit)
    page = messages[start:end]
    has_more = start > 0
    next_before = str(page[0].get("message_id") or "") if has_more and page else ""
    return page, has_more, next_before


def _conversation_message(
    state_dir: Path,
    message: dict[str, Any],
    *,
    contribution_by_request: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    refs = message.get("refs") if isinstance(message.get("refs"), dict) else {}
    request_id = str(refs.get("request_id") or "")
    role = str(message.get("role") or "")
    text = str(message.get("text") or "")
    synthesis_repair_id = str(refs.get("synthesis_repair_id") or "")
    if synthesis_repair_id:
        try:
            revision = max(
                1,
                int(refs.get("synthesis_repair_revision") or 1),
            )
        except (TypeError, ValueError):
            revision = 1
        role = "system"
        text = (
            "ZaoFu retried synthesis after contract validation failed "
            f"(revision {revision})."
        )
    if role == "assistant" or request_id:
        text = hydrate_channel_message_text(state_dir, message)
        text = structured_reply_display_text(text, "channel_contribution")
    out = {
        key: message.get(key)
        for key in (
            "message_id",
            "client_message_id",
            "idempotency_key",
            "reply_to_message_id",
            "thread_id",
            "event_id",
            "ts",
            "actor",
            "member_id",
            "role",
            "source",
            "body_ref",
            "body_sha256",
            "body_byte_count",
            "mentions",
            "mention_tokens",
            "origin",
            "delivery",
            "pinned",
        )
        if key in message
    }
    out["text"] = text
    if synthesis_repair_id:
        out["role"] = "system"
        out["member_id"] = "zf-kernel"
        out["actor"] = "zf-kernel"
    out["refs"] = _compact_refs(refs)
    contribution = contribution_by_request.get(request_id)
    if contribution:
        out["structured_contribution"] = contribution
    return out


def _conversation_contribution(
    state_dir: Path,
    item: dict[str, Any],
) -> dict[str, Any]:
    artifact_ref = str(item.get("artifact_ref") or "")
    artifact_digest = str(item.get("artifact_digest") or "")
    body: dict[str, Any] = {}
    if artifact_ref:
        try:
            hydrated = hydrate_sidecar_ref(state_dir, {
                "kind": "channel_contribution",
                "ref": artifact_ref,
                "sha256": artifact_digest,
                "content_type": "application/json",
                "schema_version": CONTRIBUTION_SCHEMA_VERSION,
                "encoding": "utf-8",
            })
            payload = hydrated.payload if isinstance(hydrated.payload, dict) else {}
            body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
        except SidecarRefError:
            body = {}
    return {
        "request_id": str(item.get("request_id") or ""),
        "message_id": str(item.get("message_id") or ""),
        "member_id": str(item.get("member_id") or ""),
        "thread_id": str(item.get("thread_id") or "main"),
        "contract_status": str(item.get("contract_status") or ""),
        "summary": _limited_text(body.get("summary"), 720),
        "findings": _semantic_items(body.get("findings")),
        "contradictions": _semantic_items(body.get("contradictions")),
        "risks": _semantic_items(body.get("risks")),
        "questions": _semantic_items(body.get("questions") or body.get("open_questions")),
        "artifact_ref": artifact_ref,
        "artifact_digest": artifact_digest,
        "source_refs": [str(value) for value in item.get("source_refs") or []][:12],
        "evidence_refs": [str(value) for value in item.get("evidence_refs") or []][:12],
    }


def _semantic_items(value: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in value if isinstance(value, list) else []:
        if isinstance(raw, dict):
            text = next((
                str(raw.get(key) or "").strip()
                for key in ("text", "statement", "risk", "question", "summary", "description")
                if str(raw.get(key) or "").strip()
            ), "")
            label = str(raw.get("type") or raw.get("priority") or raw.get("category") or "")
            identity = str(raw.get("id") or raw.get("question_id") or "")
        else:
            text = str(raw or "").strip()
            label = ""
            identity = ""
        if text:
            out.append({
                "id": identity,
                "label": label,
                "text": _limited_text(text, 480),
            })
        if len(out) >= 8:
            break
    return out


def _compact_refs(refs: dict[str, Any]) -> dict[str, Any]:
    out = {
        key: refs.get(key)
        for key in (
            "request_id",
            "run_id",
            "provider_session_id",
            "artifact_ref",
            "artifact_digest",
            "synthesis_request_id",
            "synthesis_repair_id",
            "synthesis_repair_revision",
        )
        if refs.get(key) not in (None, "")
    }
    body = refs.get("message_body")
    if isinstance(body, dict):
        out["message_body"] = {
            key: body.get(key)
            for key in ("kind", "ref", "sha256", "byte_count", "content_type", "schema_version")
            if body.get(key) not in (None, "")
        }
    return out


def _request_in_conversation(
    item: dict[str, Any],
    *,
    page_message_ids: set[str],
    page_request_ids: set[str],
) -> bool:
    status = str(item.get("status") or "")
    return (
        status in _ACTIVE_RUN_STATUSES
        or status in _FAILED_RUN_STATUSES
        or str(item.get("message_id") or "") in page_message_ids
        or str(item.get("request_id") or "") in page_request_ids
    )


def _provider_run_in_conversation(
    item: dict[str, Any],
    *,
    page_message_ids: set[str],
    page_request_ids: set[str],
) -> bool:
    status = str(item.get("live_status") or item.get("status") or "")
    return (
        status in _ACTIVE_RUN_STATUSES
        or status in _FAILED_RUN_STATUSES
        or (
            status not in {"completed", "succeeded", "done"}
            and (
                str(item.get("message_id") or "") in page_message_ids
                or str(item.get("request_id") or "") in page_request_ids
            )
        )
    )


def _compact_reply_request(item: dict[str, Any]) -> dict[str, Any]:
    return _compact_mapping(item, text_limit=480, drop={"usage", "raw_output", "prompt"})


def _compact_provider_run(item: dict[str, Any]) -> dict[str, Any]:
    out = _compact_mapping(
        item,
        text_limit=480,
        drop={"usage", "raw_output", "prompt", "transcript"},
    )
    parts = item.get("parts") if isinstance(item.get("parts"), list) else []
    out["parts"] = [
        _compact_mapping(part, text_limit=900, drop={"raw_output", "arguments"})
        for part in parts[-24:]
        if isinstance(part, dict)
    ]
    return out


def _compact_mapping(
    item: dict[str, Any],
    *,
    text_limit: int,
    drop: set[str] | None = None,
) -> dict[str, Any]:
    ignored = drop or set()
    out: dict[str, Any] = {}
    for key, value in item.items():
        if key in ignored:
            continue
        if isinstance(value, str):
            out[key] = _limited_text(value, text_limit)
        elif isinstance(value, (bool, int, float)) or value is None:
            out[key] = value
        elif key == "refs" and isinstance(value, dict):
            out[key] = _compact_refs(value)
        elif isinstance(value, list) and all(isinstance(part, str) for part in value):
            out[key] = [str(part)[:text_limit] for part in value[:24]]
        elif isinstance(value, dict):
            out[key] = {
                child_key: _limited_text(child_value, text_limit)
                if isinstance(child_value, str) else child_value
                for child_key, child_value in list(value.items())[:24]
                if not isinstance(child_value, (list, dict))
            }
    return out


def _compact_rows(value: Any, *, limit: int) -> list[dict[str, Any]]:
    rows = [item for item in value or [] if isinstance(item, dict)]
    return [_compact_mapping(item, text_limit=600) for item in rows[-limit:]]


def _selected_rows(value: Any, message_ids: set[str]) -> list[dict[str, Any]]:
    return [
        _compact_mapping(item, text_limit=480)
        for item in value or []
        if isinstance(item, dict)
        and (
            not str(item.get("message_id") or "")
            or str(item.get("message_id") or "") in message_ids
        )
    ][-30:]


def _limited_text(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


__all__ = [
    "DEFAULT_CONVERSATION_LIMIT",
    "MAX_CONVERSATION_LIMIT",
    "empty_channel_conversation",
    "project_channel_conversation",
]
