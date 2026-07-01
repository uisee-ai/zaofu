"""Bounded context packs for Agent Channel replies."""

from __future__ import annotations

import hashlib
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.channel_contracts import normalize_channel_skill_refs, normalize_permission_profile
from zf.runtime.channel_roles import (
    load_role_definition_excerpt,
    normalize_role_context_ref,
)


def build_channel_context_pack(
    channel: dict[str, Any] | None,
    *,
    channel_id: str,
    thread_id: str,
    target_member_id: str,
    trigger_message_id: str,
    visibility_profile: str = "",
    channel_role: str = "",
    role_context_ref: str = "",
    skill_refs: object = None,
    permission_profile: str = "",
    max_messages: int = 8,
    max_text_chars: int = 4000,
) -> dict[str, Any]:
    """Build a compact, reference-oriented context packet for one reply.

    The pack deliberately carries bounded excerpts and message/event refs rather
    than the full transcript. Provider-specific adapters can rehydrate more
    context later through the same event-log truth if they are allowed to.
    """
    profile = visibility_profile or "minimal"
    normalized_permission_profile = normalize_permission_profile(permission_profile)
    safe_role_context_ref = normalize_role_context_ref(role_context_ref)
    safe_skill_refs = normalize_channel_skill_refs(skill_refs)
    profile_limits = _profile_limits(profile, max_messages=max_messages, max_text_chars=max_text_chars)
    selected = _select_messages(
        list((channel or {}).get("messages") or (channel or {}).get("recent_messages") or []),
        thread_id=thread_id,
        trigger_message_id=trigger_message_id,
        max_messages=profile_limits["max_messages"],
        max_text_chars=profile_limits["max_text_chars"],
    )
    context_pack_id = _stable_context_pack_id(
        channel_id,
        thread_id,
        trigger_message_id,
        target_member_id,
    )
    payload = {
        "context_pack_id": context_pack_id,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "target_member_id": target_member_id,
        "trigger_message_id": trigger_message_id,
        "visibility_profile": profile,
        "channel_role": channel_role,
        "permission_profile": normalized_permission_profile,
        "role_context_ref": safe_role_context_ref,
        "skill_refs": safe_skill_refs,
        "skill_metadata": _skill_metadata(safe_skill_refs),
        "role_definition": load_role_definition_excerpt(
            safe_role_context_ref,
            max_chars=profile_limits["role_definition_chars"],
        ),
        "summary": str((channel or {}).get("summary") or ""),
        "message_refs": [
            {
                "message_id": str(item.get("message_id") or ""),
                "event_id": str(item.get("event_id") or ""),
                "member_id": str(item.get("member_id") or ""),
                "role": str(item.get("role") or ""),
                "source": str(item.get("source") or ""),
                "text_excerpt": _excerpt(str(item.get("text") or ""), 240),
            }
            for item in selected
        ],
        "artifact_refs": _select_artifact_refs(
            channel,
            selected_messages=selected,
            visibility_profile=profile,
        ),
        "report_refs": _select_report_refs(channel, visibility_profile=profile),
        "limits": {
            "max_messages": profile_limits["max_messages"],
            "max_text_chars": profile_limits["max_text_chars"],
            "max_role_definition_chars": profile_limits["role_definition_chars"],
            "max_skill_refs": 8,
            "selected_messages": len(selected),
            "visibility_profile": profile,
        },
    }
    return redact_obj(payload)


def context_pack_rejection_reason(
    channel: dict[str, Any] | None,
    *,
    max_source_chars: int = 60000,
    max_source_messages: int = 200,
) -> tuple[str, dict[str, int]]:
    messages = list((channel or {}).get("messages") or (channel or {}).get("recent_messages") or [])
    source_chars = sum(len(str(item.get("text") or "")) for item in messages if isinstance(item, dict))
    limits = {
        "max_source_chars": max_source_chars,
        "source_chars": source_chars,
        "max_source_messages": max_source_messages,
        "source_messages": len(messages),
    }
    if source_chars > max_source_chars:
        return "channel transcript exceeds context source budget", limits
    if len(messages) > max_source_messages:
        return "channel transcript exceeds context message budget", limits
    return "", limits


def _skill_metadata(skill_refs: list[str]) -> list[dict[str, str]]:
    return [
        {
            "ref": ref,
            "skill": ref.split("/")[1] if len(ref.split("/")) > 2 else ref,
            "source": "channel_member",
            "status": "referenced",
        }
        for ref in skill_refs[:8]
    ]


def _select_messages(
    messages: list[dict[str, Any]],
    *,
    thread_id: str,
    trigger_message_id: str,
    max_messages: int,
    max_text_chars: int,
) -> list[dict[str, Any]]:
    same_thread = [
        item for item in messages
        if str(item.get("thread_id") or "main") == thread_id
    ]
    if not same_thread:
        same_thread = messages
    tail = same_thread[-max_messages:]
    selected: list[dict[str, Any]] = []
    used = 0
    for item in reversed(tail):
        text = str(item.get("text") or "")
        remaining = max(max_text_chars - used, 0)
        if remaining <= 0:
            break
        trimmed = dict(item)
        trimmed["text"] = _excerpt(text, min(remaining, 800))
        used += len(str(trimmed.get("text") or ""))
        selected.append(trimmed)
        if str(item.get("message_id") or "") == trigger_message_id:
            continue
    return list(reversed(selected))


def _stable_context_pack_id(
    channel_id: str,
    thread_id: str,
    trigger_message_id: str,
    target_member_id: str,
) -> str:
    digest = hashlib.sha1(
        f"{channel_id}:{thread_id}:{trigger_message_id}:{target_member_id}".encode("utf-8"),
    ).hexdigest()[:16]
    return f"ctx-{digest}"


def _profile_limits(profile: str, *, max_messages: int, max_text_chars: int) -> dict[str, int]:
    defaults = {
        "minimal": {"max_messages": 6, "max_text_chars": 2400},
        "planner": {"max_messages": 12, "max_text_chars": 6000},
        "reviewer": {"max_messages": 10, "max_text_chars": 5000},
        "owner_report": {"max_messages": 8, "max_text_chars": 4000},
        "full_audit": {"max_messages": 20, "max_text_chars": 10000},
    }
    selected = defaults.get(profile, defaults["minimal"])
    role_definition_chars = {
        "minimal": 500,
        "planner": 900,
        "reviewer": 900,
        "owner_report": 700,
        "full_audit": 1200,
    }.get(profile, 500)
    return {
        "max_messages": min(max(selected["max_messages"], 1), max(max_messages, selected["max_messages"])),
        "max_text_chars": min(max(selected["max_text_chars"], 1), max(max_text_chars, selected["max_text_chars"])),
        "role_definition_chars": role_definition_chars,
    }


def _select_report_refs(channel: dict[str, Any] | None, *, visibility_profile: str) -> list[dict[str, str]]:
    if visibility_profile != "owner_report":
        return []
    reports: list[dict[str, str]] = []
    for item in list((channel or {}).get("owner_reports") or [])[-3:]:
        if isinstance(item, dict):
            reports.append({
                "type": "owner_report",
                "report_id": str(item.get("report_id") or ""),
                "event_id": str(item.get("event_id") or ""),
                "summary_excerpt": _excerpt(str(item.get("summary") or ""), 240),
            })
    for item in list((channel or {}).get("automation_reports") or [])[-3:]:
        if isinstance(item, dict):
            reports.append({
                "type": "automation_report",
                "report_id": str(item.get("report_id") or ""),
                "event_id": str(item.get("event_id") or ""),
                "summary_excerpt": _excerpt(str(item.get("summary") or ""), 240),
            })
    return reports


def _select_artifact_refs(
    channel: dict[str, Any] | None,
    *,
    selected_messages: list[dict[str, Any]],
    visibility_profile: str,
) -> list[dict[str, Any]]:
    selected_message_ids = {
        str(item.get("message_id") or "")
        for item in selected_messages
        if str(item.get("message_id") or "")
    }
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(ref: dict[str, Any]) -> None:
        key = (
            str(ref.get("artifact_id") or "")
            or str(ref.get("attachment_id") or "")
            or f"{ref.get('type')}:{ref.get('message_id')}:{ref.get('name')}"
        )
        if not key or key in seen:
            return
        seen.add(key)
        refs.append(redact_obj(ref))

    for message in selected_messages:
        message_refs = message.get("refs") if isinstance(message.get("refs"), dict) else {}
        for attachment in _dict_list(message_refs.get("attachments")):
            add(_attachment_ref(attachment, message=message))
        for artifact in _dict_list(message_refs.get("artifacts")):
            add(_artifact_ref(artifact, message=message))

    for attachment in _dict_list((channel or {}).get("attachments")):
        message_id = str(attachment.get("message_id") or "")
        if selected_message_ids and message_id and message_id not in selected_message_ids:
            continue
        add(_attachment_ref(attachment))

    artifact_limit = 12 if visibility_profile == "full_audit" else 8
    artifact_items = _dict_list((channel or {}).get("artifacts"))[-artifact_limit:]
    for artifact in artifact_items:
        if str(artifact.get("status") or "") == "rejected":
            continue
        message_id = str(artifact.get("message_id") or "")
        if selected_message_ids and message_id and message_id not in selected_message_ids:
            continue
        add(_artifact_ref(artifact))
    for update in _dict_list((channel or {}).get("state_updates"))[-artifact_limit:]:
        update_refs = update.get("refs") if isinstance(update.get("refs"), dict) else {}
        for artifact in _dict_list(update_refs.get("artifact_refs")):
            add(_artifact_ref({
                **artifact,
                "event_id": update.get("event_id"),
                "thread_id": update.get("thread_id"),
                "summary": artifact.get("summary") or update.get("summary"),
            }))
        for key in (
            "workflow_prompt_ref",
            "workflow_input_manifest_ref",
            "research_artifact_ref",
            "artifact_ref",
            "report_ref",
            "path",
            "ref",
        ):
            value = str(update_refs.get(key) or "").strip()
            if not value:
                continue
            add(_artifact_ref({
                "artifact_id": f"{key}:{value}",
                "event_id": update.get("event_id"),
                "thread_id": update.get("thread_id"),
                "name": key,
                "kind": key,
                "path": value if "/" in value else "",
                "uri": value if "://" in value else "",
                "summary": update.get("summary"),
                "status": "attached",
            }))
    return refs[-artifact_limit:]


def _attachment_ref(item: dict[str, Any], *, message: dict[str, Any] | None = None) -> dict[str, Any]:
    message_id = str(item.get("message_id") or (message or {}).get("message_id") or "")
    return {
        "type": "attachment",
        "attachment_id": str(item.get("attachment_id") or item.get("id") or ""),
        "artifact_id": str(item.get("artifact_id") or ""),
        "event_id": str(item.get("event_id") or (message or {}).get("event_id") or ""),
        "thread_id": str(item.get("thread_id") or (message or {}).get("thread_id") or "main"),
        "message_id": message_id,
        "member_id": str(item.get("member_id") or (message or {}).get("member_id") or ""),
        "name": str(item.get("name") or item.get("filename") or ""),
        "mime": str(item.get("mime") or item.get("type") or item.get("content_type") or ""),
        "size": _safe_int(item.get("size") if item.get("size") is not None else item.get("bytes")),
        "hash": str(item.get("hash") or item.get("sha256") or ""),
        "uri": str(item.get("uri") or ""),
        "status": str(item.get("status") or "uploaded"),
    }


def _artifact_ref(item: dict[str, Any], *, message: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "artifact",
        "artifact_id": str(item.get("artifact_id") or item.get("id") or ""),
        "attachment_id": str(item.get("attachment_id") or ""),
        "event_id": str(item.get("event_id") or (message or {}).get("event_id") or ""),
        "thread_id": str(item.get("thread_id") or (message or {}).get("thread_id") or "main"),
        "message_id": str(item.get("message_id") or (message or {}).get("message_id") or ""),
        "member_id": str(item.get("member_id") or (message or {}).get("member_id") or ""),
        "name": str(item.get("name") or item.get("filename") or ""),
        "kind": str(item.get("kind") or ""),
        "mime": str(item.get("mime") or item.get("type") or item.get("content_type") or ""),
        "size": _safe_int(item.get("size") if item.get("size") is not None else item.get("bytes")),
        "hash": str(item.get("hash") or item.get("sha256") or ""),
        "path": str(item.get("path") or ""),
        "uri": str(item.get("uri") or ""),
        "status": str(item.get("status") or ""),
        "summary_excerpt": _excerpt(str(item.get("summary") or ""), 240),
    }


def _dict_list(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [dict(item) for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _safe_int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."
