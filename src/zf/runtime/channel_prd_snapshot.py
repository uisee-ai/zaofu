"""Immutable product inputs for a user-requested PRD generation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref, write_sidecar_json

INPUT_FIELDS = ("messages", "recent_messages", "open_questions", "contributions", "findings", "cross_reviews", "attachments")


def capture_prd_input(state_dir, channel, *, request_id, thread_id, actor, source_event_id, additional_material=""):
    body = {"schema_version": "channel.prd.input.v1", "channel_id": channel["channel_id"], "thread_id": thread_id,
            "request_id": request_id, "cutoff_at": datetime.now(timezone.utc).isoformat(),
            "cutoff_seq": channel.get("seq", 0), "additional_material": additional_material,
            "inputs": {key: channel.get(key, []) for key in INPUT_FIELDS}}
    if additional_material:
        from zf.runtime.channel_sidecar import channel_message_event_payload
        material = channel_message_event_payload(state_dir, {
            "channel_id": channel["channel_id"], "thread_id": thread_id,
            "message_id": f"material-{request_id}", "member_id": actor, "role": "user",
            "source": "selected_material", "text": additional_material,
            "refs": {"selected_material": True, "source_action_event_id": source_event_id},
        }, created_by=actor, source_event_id=source_event_id)
        body["inputs"]["messages"] = [*body["inputs"].get("messages", []), {**material, "event_id": source_event_id}]
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    if len(raw) > 3 * 1024 * 1024:
        raise SidecarRefError("payload_too_large", "本次材料超过输入快照限制，请缩小材料范围")
    digest = hashlib.sha256(raw).hexdigest()
    descriptor = write_sidecar_json(state_dir, f"channels/prd-inputs/{digest}.json", body,
                                    kind="channel_prd_input", schema_version=body["schema_version"],
                                    created_by=actor, source_event_id=source_event_id, required=True)
    return {**descriptor, "cutoff_at": body["cutoff_at"], "cutoff_seq": body["cutoff_seq"], "input_request_id": request_id}


def snapshot_channel(state_dir, channel, request_id, *, trigger_message=None):
    request = next((r for r in channel.get("synthesis_requests", []) if r.get("request_id") == request_id), {})
    descriptor = request.get("prd_input_snapshot")
    if not descriptor:
        return channel
    body = hydrate_sidecar_ref(state_dir, descriptor, purpose="channel-prd-input", actor="channel-runtime").payload
    if body.get("channel_id") != channel.get("channel_id") or body.get("request_id") != descriptor.get("input_request_id", request_id) or body.get("thread_id") != request.get("thread_id", "main"):
        raise SidecarRefError("snapshot_binding_mismatch", "PRD材料快照与本次请求不匹配")
    result = {**channel, **body["inputs"], "prd_input_snapshot": descriptor,
              "prd_additional_material": body.get("additional_material", "")}
    if trigger_message:
        result["messages"] = [*result.get("messages", []), trigger_message]
    return result
