"""Read version-bound Channel PRD input identities without inferring their meaning."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.channel_attachments import read_channel_attachment
from zf.runtime.channel_owner_sources import is_owner_message
from zf.runtime.channel_sidecar import hydrate_channel_message_text
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


def prd_source_view(state_dir: Path, channel: dict[str, Any], events: list[ZfEvent],
                    thread_id: str, synthesis: dict[str, Any]) -> dict[str, Any]:
    """Hydrate only owner inputs included in this immutable PRD's context manifest."""
    coverage = synthesis.get("source_coverage")
    if not isinstance(coverage, list):
        return {"status": "unavailable", "items": []}
    channel_id = str(channel.get("channel_id") or "")
    owner = str(channel.get("owner_actor_ref") or "")
    owner_events = {str((event.payload or {}).get("message_id") or ""): event.id for event in events
                    if is_owner_message(event, channel_id=channel_id, thread_id=thread_id, owner=owner)}
    thread_messages = {str(row.get("message_id") or ""): row for row in channel.get("messages") or []
                       if isinstance(row, dict) and str(row.get("thread_id") or "main") == thread_id}
    messages = {message_id: row for message_id, row in thread_messages.items()
                if row.get("role") == "user" and owner_events.get(message_id) == row.get("event_id")}
    items: list[dict[str, Any]] = []
    missing = False
    for row in coverage:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        message_id = str(row.get("message_id") or "")
        if kind not in {"requirement", "owner_input", "channel_attachment"}:
            continue
        if message_id not in messages:
            if not thread_messages.get(message_id) or thread_messages[message_id].get("role") == "user":
                missing = True
            continue
        message = messages[message_id]
        digest = str(row.get("message_body_digest") or "").removeprefix("sha256:")
        item: dict[str, Any] = {"kind": kind, "message_id": message_id, "digest": digest,
                                "name": str(row.get("name") or ""), "content": "", "available": False}
        try:
            if kind == "channel_attachment":
                uri = str(row.get("message_body_ref") or "")
                attachment = next(ref for ref in (message.get("refs") or {}).get("attachments") or []
                                  if isinstance(ref, dict) and ref.get("uri") == uri
                                  and str(ref.get("hash") or "") == digest)
                _, data = read_channel_attachment(state_dir, attachment, channel_id=channel_id, thread_id=thread_id)
                content = data.decode("utf-8")
            else:
                if row.get("event_id") and row.get("event_id") != message.get("event_id"):
                    raise ValueError("message event mismatch")
                descriptor = (message.get("refs") or {}).get("message_body") or {}
                if descriptor:
                    if (str(descriptor.get("sha256") or "").removeprefix("sha256:") != digest
                            or str(descriptor.get("ref") or "") != str(row.get("message_body_ref") or "")):
                        raise ValueError("message digest mismatch")
                    content = hydrate_channel_message_text(state_dir, message, strict=True)
                else:
                    content = hydrate_channel_message_text(state_dir, message, strict=True)
                    if hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
                        raise ValueError("inline message digest mismatch")
            item.update(content=content, available=True)
        except (ValueError, KeyError, StopIteration, UnicodeError, SidecarRefError):
            pass
        items.append(item)
    # File-to-PRD sources are accepted only when a scoped request bound the exact snapshot.
    for row in coverage:
        if not isinstance(row, dict) or row.get("kind") != "channel_prd_source":
            continue
        ref, digest = str(row.get("artifact_ref") or ""), str(row.get("artifact_digest") or "")
        descriptor = next(((event.payload or {}).get("prd_source_file") for event in events
                           if event.type == "channel.synthesis.requested" and event.correlation_id == channel_id
                           and str((event.payload or {}).get("thread_id") or "main") == thread_id
                           and isinstance((event.payload or {}).get("prd_source_file"), dict)
                           and (event.payload or {})["prd_source_file"].get("ref") == ref
                           and (event.payload or {})["prd_source_file"].get("sha256") == digest
                           and (event.payload or {})["prd_source_file"].get("access_scope") == {"channel_id": channel_id, "thread_id": thread_id}), None)
        if not descriptor:
            missing = True
            continue
        item = {"kind": "channel_prd_source", "message_id": "", "digest": digest,
                "name": "原始需求文件", "content": "", "available": False}
        try:
            hydrated = hydrate_sidecar_ref(state_dir, descriptor, purpose="channel-prd-source-preview", actor="web")
            if isinstance(hydrated.payload, str):
                item.update(content=hydrated.payload, available=True)
        except SidecarRefError:
            pass
        items.append(item)
    return {"status": "ready" if not missing and all(item["available"] for item in items) else "incomplete", "items": items}
