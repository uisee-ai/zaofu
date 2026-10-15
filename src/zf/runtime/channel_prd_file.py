"""Snapshot an explicitly selected Channel file for the ordinary PRD contract."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.core.events import EventLog
from zf.runtime.channel_document_source import ChannelDocumentError, read_channel_document
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_text, SidecarRefError


def snapshot_prd_file(*, project_root: Path | None, state_dir: Path, channel: dict,
                      thread_id: str, selection: Any, created_by: str, event_id: str) -> dict:
    if not isinstance(selection, dict) or set(selection) != {"ref", "sha256"}:
        raise ChannelDocumentError(422, "source_file requires ref and sha256")
    if project_root is None:
        raise ChannelDocumentError(422, "Project root is required for a source file")
    if not all(isinstance(selection.get(key), str) and selection[key] for key in ("ref", "sha256")):
        raise ChannelDocumentError(422, "source_file requires non-empty ref and sha256")
    attention = (channel.get("discussion_attention") or {}).get(thread_id) or {}
    busy = any(str(row.get("thread_id") or "main") == thread_id
               and row.get("status") in {"requested", "pending", "queued", "running", "started"}
               for row in channel.get("reply_requests") or [])
    if busy or attention.get("active_reply_count") or attention.get("pending_member_invitation_count"):
        raise ChannelDocumentError(409, "Wait for active replies and member admissions before publishing a draft")
    questions = channel.get("open_questions") or {}
    questions = questions.values() if isinstance(questions, dict) else questions
    if any(str(q.get("thread_id") or "main") == thread_id and q.get("status") == "open"
           for q in questions):
        raise ChannelDocumentError(409, "Resolve current questions before publishing a draft")
    doc = read_channel_document(project_root=project_root, state_dir=state_dir,
                                channel=channel, thread_id=thread_id, ref=selection["ref"])
    if doc["format"] != "markdown" or not doc["content"].strip():
        raise ChannelDocumentError(422, "PRD source must be a non-empty Markdown document")
    if doc["sha256"] != selection["sha256"].removeprefix("sha256:"):
        raise ChannelDocumentError(409, "Source file changed; preview it again before publishing")
    channel_id = str(channel["channel_id"])
    identity = hashlib.sha256(f"{channel_id}:{thread_id}:{doc['sha256']}".encode()).hexdigest()
    return write_sidecar_text(
        state_dir, f"channels/prd-sources/{identity}.md", doc["content"],
        kind="channel_prd_source", schema_version="channel.prd-source.v1",
        content_type="text/markdown", created_by=created_by, source_event_id=event_id,
        access_scope={"channel_id": channel_id, "thread_id": thread_id}, required=True,
    )


def expand_prd_file(synthesis: dict, *, request: dict, state_dir: Path) -> tuple[dict, str]:
    selected = request.get("prd_source_file")
    declared = synthesis.get("prd_file")
    if declared is None:
        return synthesis, ""
    if "prd_markdown" in synthesis or "prd_composition" in synthesis:
        return synthesis, "provide only one of prd_file, prd_markdown, prd_composition"
    if not isinstance(selected, dict) or not isinstance(declared, dict) or set(declared) != {"ref", "sha256"}:
        return synthesis, "prd_file requires the source snapshot bound to this synthesis request"
    bound = next((event.payload.get("prd_source_file") for event in EventLog(state_dir / "events.jsonl").read_all()
                  if event.type == "channel.synthesis.requested"
                  and event.payload.get("request_id") == request.get("synthesis_request_id")
                  and event.payload.get("channel_id") == request.get("channel_id")
                  and str(event.payload.get("thread_id") or "main") == str(request.get("thread_id") or "main")), None)
    if not bound or bound != selected:
        return synthesis, "prd_file is not bound to a durable synthesis request in this thread"
    if any(declared.get(key) != selected.get(key) for key in ("ref", "sha256")):
        return synthesis, "prd_file identity/digest does not match the selected source snapshot"
    try:
        body = hydrate_sidecar_ref(state_dir, selected).payload
    except SidecarRefError as exc:
        return synthesis, f"PRD source snapshot cannot be verified: {exc.code}"
    if not isinstance(body, str) or not body.strip():
        return synthesis, "PRD source snapshot has no Markdown body"
    return {**synthesis, "prd_markdown": body}, ""
