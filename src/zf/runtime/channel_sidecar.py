"""Sidecar helpers for Channel/Kanban conversation payloads."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.sidecar_refs import (
    SidecarRefError,
    hydrate_sidecar_ref,
    write_sidecar_json,
)


CHANNEL_MESSAGE_BODY_SCHEMA_VERSION = "channel.message.body.v1"
CHANNEL_MESSAGE_POSTED_SCHEMA_VERSION = "channel.message.posted.v2"
CHANNEL_CONTEXT_PACK_SCHEMA_VERSION = "channel.context_pack.body.v1"
CHANNEL_CONTEXT_PACK_EVENT_SCHEMA_VERSION = "channel.context_pack.built.v2"
CHANNEL_MESSAGE_PREVIEW_CHARS = 2400
CHANNEL_CONTEXT_SUMMARY_PREVIEW_CHARS = 1200


def channel_message_event_payload(
    state_dir: Path,
    payload: dict[str, Any],
    *,
    created_by: str,
    source_event_id: str = "",
) -> dict[str, Any]:
    """Return a sidecar-backed `channel.message.posted` envelope."""

    full_text = _message_text(payload)
    channel_id = str(payload.get("channel_id") or "").strip() or "unknown-channel"
    message_id = str(payload.get("message_id") or "").strip() or _stable_id("msg", full_text)
    body = redact_obj({
        "schema_version": CHANNEL_MESSAGE_BODY_SCHEMA_VERSION,
        "channel_id": channel_id,
        "thread_id": str(payload.get("thread_id") or "main"),
        "message_id": message_id,
        "member_id": str(payload.get("member_id") or ""),
        "role": str(payload.get("role") or ""),
        "source": str(payload.get("source") or ""),
        "text": full_text,
        "mentions": payload.get("mentions") if isinstance(payload.get("mentions"), list) else [],
        "mention_tokens": (
            payload.get("mention_tokens") if isinstance(payload.get("mention_tokens"), list) else []
        ),
        "refs": payload.get("refs") if isinstance(payload.get("refs"), dict) else {},
    })
    body_text = _json_text(body)
    digest = hashlib.sha256(body_text.encode("utf-8", errors="replace")).hexdigest()
    ref = (
        PurePosixPath("channels")
        / _safe_segment(channel_id)
        / "messages"
        / f"{_safe_segment(message_id)}-{digest[:16]}.json"
    )
    preview = _preview(full_text, CHANNEL_MESSAGE_PREVIEW_CHARS)
    descriptor = write_sidecar_json(
        state_dir,
        ref,
        body,
        kind="channel_message_body",
        schema_version=CHANNEL_MESSAGE_BODY_SCHEMA_VERSION,
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "message_id": message_id,
        },
        retention={"class": "audit_required", "redaction_profile": "default"},
        required=True,
        preview=preview,
    )
    refs = dict(payload.get("refs") if isinstance(payload.get("refs"), dict) else {})
    refs["message_body"] = descriptor
    envelope = dict(payload)
    envelope.update({
        "schema_version": CHANNEL_MESSAGE_POSTED_SCHEMA_VERSION,
        "message_id": message_id,
        "text": preview,
        "text_preview": preview,
        "body_ref": descriptor["ref"],
        "body_sha256": descriptor["sha256"],
        "body_byte_count": descriptor["byte_count"],
        "refs": refs,
    })
    if envelope.get("message") == full_text:
        envelope["message"] = preview
    return envelope


def channel_context_pack_event_payload(
    state_dir: Path,
    payload: dict[str, Any],
    *,
    created_by: str,
    source_event_id: str = "",
) -> dict[str, Any]:
    """Return a sidecar-backed `channel.context_pack.built` envelope."""

    context_pack_id = str(payload.get("context_pack_id") or "").strip() or _stable_id(
        "ctx",
        _json_text(payload),
    )
    channel_id = str(payload.get("channel_id") or "").strip() or "unknown-channel"
    body = redact_obj({
        **payload,
        "schema_version": CHANNEL_CONTEXT_PACK_SCHEMA_VERSION,
        "context_pack_id": context_pack_id,
        "channel_id": channel_id,
    })
    body_text = _json_text(body)
    digest = hashlib.sha256(body_text.encode("utf-8", errors="replace")).hexdigest()
    ref = (
        PurePosixPath("channels")
        / _safe_segment(channel_id)
        / "context-packs"
        / f"{_safe_segment(context_pack_id)}-{digest[:16]}.json"
    )
    descriptor = write_sidecar_json(
        state_dir,
        ref,
        body,
        kind="channel_context_pack",
        schema_version=CHANNEL_CONTEXT_PACK_SCHEMA_VERSION,
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "context_pack_id": context_pack_id,
        },
        retention={"class": "audit_required", "redaction_profile": "default"},
        required=True,
        preview=_preview(str(payload.get("summary") or context_pack_id), CHANNEL_CONTEXT_SUMMARY_PREVIEW_CHARS),
    )
    refs = dict(payload.get("refs") if isinstance(payload.get("refs"), dict) else {})
    refs["context_pack"] = descriptor
    return {
        "schema_version": CHANNEL_CONTEXT_PACK_EVENT_SCHEMA_VERSION,
        "context_pack_id": context_pack_id,
        "channel_id": channel_id,
        "thread_id": str(payload.get("thread_id") or "main"),
        "target_member_id": str(payload.get("target_member_id") or ""),
        "trigger_message_id": str(payload.get("trigger_message_id") or ""),
        "visibility_profile": str(payload.get("visibility_profile") or ""),
        "channel_role": str(payload.get("channel_role") or ""),
        "permission_profile": str(payload.get("permission_profile") or ""),
        "role_context_ref": str(payload.get("role_context_ref") or ""),
        "skill_refs": payload.get("skill_refs") if isinstance(payload.get("skill_refs"), list) else [],
        "summary": _preview(str(payload.get("summary") or ""), CHANNEL_CONTEXT_SUMMARY_PREVIEW_CHARS),
        "routing_reason": str(payload.get("routing_reason") or ""),
        "source": str(payload.get("source") or ""),
        "limits": payload.get("limits") if isinstance(payload.get("limits"), dict) else {},
        "message_ref_count": len(payload.get("message_refs") if isinstance(payload.get("message_refs"), list) else []),
        "artifact_ref_count": len(payload.get("artifact_refs") if isinstance(payload.get("artifact_refs"), list) else []),
        "report_ref_count": len(payload.get("report_refs") if isinstance(payload.get("report_refs"), list) else []),
        "context_pack_ref": descriptor["ref"],
        "context_pack_sha256": descriptor["sha256"],
        "context_pack_byte_count": descriptor["byte_count"],
        "refs": refs,
    }


def hydrate_channel_message_text(
    state_dir: Path,
    payload: dict[str, Any],
    *,
    strict: bool = False,
) -> str:
    descriptor = _sidecar_descriptor(payload, "message_body", "body_ref")
    if descriptor:
        try:
            hydrated = hydrate_sidecar_ref(state_dir, descriptor)
            body = hydrated.payload if isinstance(hydrated.payload, dict) else {}
            text = _message_text(body)
            if text:
                return text
        except SidecarRefError:
            if strict:
                raise
    return _message_text(payload)


def hydrate_channel_context_pack_payload(
    state_dir: Path,
    payload: dict[str, Any],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    descriptor = _sidecar_descriptor(payload, "context_pack", "context_pack_ref")
    if descriptor:
        try:
            hydrated = hydrate_sidecar_ref(state_dir, descriptor)
            if isinstance(hydrated.payload, dict):
                merged = dict(hydrated.payload)
                for key, value in payload.items():
                    if key in {
                        "schema_version",
                        "context_pack_ref",
                        "context_pack_sha256",
                        "context_pack_byte_count",
                        "refs",
                    }:
                        merged[key] = value
                    elif key not in merged:
                        merged[key] = value
                return merged
        except SidecarRefError:
            if strict:
                raise
    return dict(payload)


def channel_payload_sidecar_descriptor(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return _sidecar_descriptor(payload, key, "")


def _sidecar_descriptor(payload: dict[str, Any], refs_key: str, direct_ref_key: str) -> dict[str, Any]:
    refs = payload.get("refs") if isinstance(payload.get("refs"), dict) else {}
    descriptor = refs.get(refs_key)
    if isinstance(descriptor, dict):
        return descriptor
    direct_ref = str(payload.get(direct_ref_key) or "").strip() if direct_ref_key else ""
    if not direct_ref:
        return {}
    return {
        "kind": "channel_message_body" if refs_key == "message_body" else "channel_context_pack",
        "ref": direct_ref,
        "sha256": str(payload.get("body_sha256") or payload.get("context_pack_sha256") or ""),
        "byte_count": int(payload.get("body_byte_count") or payload.get("context_pack_byte_count") or 0),
        "content_type": "application/json",
        "schema_version": (
            CHANNEL_MESSAGE_BODY_SCHEMA_VERSION
            if refs_key == "message_body"
            else CHANNEL_CONTEXT_PACK_SCHEMA_VERSION
        ),
        "encoding": "utf-8",
        "required": True,
    }


def _message_text(payload: dict[str, Any]) -> str:
    value = payload.get("text")
    if value is None:
        value = payload.get("message")
    if value is None:
        value = payload.get("text_preview")
    return str(value or "")


def _preview(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 40].rstrip() + "\n[... sidecar body truncated ...]"


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())[:96].strip(".-")
    return cleaned or "unknown"
