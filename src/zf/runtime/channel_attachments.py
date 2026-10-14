"""Durable, scope-bound Channel attachment bodies; never store bytes in events."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from zf.runtime.sidecar_refs import build_sidecar_ref_descriptor, write_sidecar_json

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_SIDECAR_BYTES = 14 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
URI_PREFIX = "channel://attachments/"


def _path(state_dir: Path, digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid Channel attachment reference")
    path = Path(state_dir).resolve()
    for part in ("channels", "attachments", f"{digest}.json"):
        path = path / part
        if path.is_symlink():
            raise ValueError("Channel attachment symlinks are not allowed")
    return path


def capture_channel_attachment(
    state_dir: Path, *, channel_id: str, thread_id: str,
    name: str, mime: str, data: bytes,
) -> dict[str, Any]:
    # Reuse the message-ingress filename, MIME and size contract.
    from zf.runtime.channel_message_ingress import _validate_ingress_attachment

    ref = {"name": name, "mime": mime, "size": len(data)}
    error = _validate_ingress_attachment(ref)
    if error:
        raise ValueError(error)
    if not data:
        raise ValueError("Channel attachment is empty")
    if mime.startswith("image/"):
        signatures = {
            "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": data.startswith(b"\xff\xd8\xff"),
            "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
        }
        if not signatures.get(mime, False):
            raise ValueError("Unsupported or mismatched image content; use PNG, JPEG, GIF or WebP")
    body = {
        "schema_version": "channel.attachment.body.v1",
        "channel_id": channel_id, "thread_id": thread_id,
        **ref, "hash": hashlib.sha256(data).hexdigest(),
        "data_base64": base64.b64encode(data).decode("ascii"),
    }
    encoded = (json.dumps(body, ensure_ascii=False, indent=2) + "\n").encode()
    digest = hashlib.sha256(encoded).hexdigest()
    target = _path(state_dir, digest)
    descriptor = write_sidecar_json(
        state_dir, target.relative_to(Path(state_dir).resolve()).as_posix(), body,
        kind="channel_attachment_body", schema_version=body["schema_version"],
        created_by="channel-upload", required=True,
        access_scope={"channel_id": channel_id, "thread_id": thread_id},
    )
    return {
        **ref, "attachment_id": f"att-{digest}", "hash": body["hash"],
        "uri": URI_PREFIX + digest, "status": "uploaded", "body_ref": descriptor,
    }


def read_channel_attachment(
    state_dir: Path, ref: dict[str, Any], *, channel_id: str, thread_id: str,
) -> tuple[dict[str, Any], bytes]:
    uri = str(ref.get("uri") or "")
    if not uri.startswith(URI_PREFIX):
        raise ValueError("Attachment has no stored content; please upload it again")
    digest = uri[len(URI_PREFIX):]
    path = _path(state_dir, digest)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_SIDECAR_BYTES + 1)
    except OSError as exc:
        raise ValueError("Attachment content is missing; please upload it again") from exc
    if len(raw) > MAX_SIDECAR_BYTES or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Channel attachment body digest/size mismatch")
    body = json.loads(raw)
    if body.get("channel_id") != channel_id or body.get("thread_id") != thread_id:
        raise ValueError("Channel attachment belongs to another channel/thread")
    for key in ("hash", "size", "name"):
        if ref.get(key) != body.get(key):
            raise ValueError(f"Channel attachment {key} mismatch")
    if (ref.get("mime") or ref.get("type")) != body.get("mime"):
        raise ValueError("Channel attachment MIME mismatch")
    data = base64.b64decode(body["data_base64"], validate=True)
    if len(data) > MAX_ATTACHMENT_BYTES or len(data) != body["size"] or hashlib.sha256(data).hexdigest() != body["hash"]:
        raise ValueError("Channel attachment content digest/size mismatch")
    body["body_ref"] = build_sidecar_ref_descriptor(
        kind="channel_attachment_body", ref=f"channels/attachments/{digest}.json",
        sha256=digest, byte_count=len(raw), content_type="application/json",
        schema_version="channel.attachment.body.v1", created_by="channel-upload", required=True,
        access_scope={"channel_id": channel_id, "thread_id": thread_id},
    )
    return body, data


def channel_image_inputs(
    state_dir: Path, *, channel: dict[str, Any], request: dict[str, Any],
) -> list[dict[str, str]]:
    """Only consume attachments selected into the recipient's context pack."""
    from zf.runtime.channel_provider_prompt import channel_context_pack_by_id

    pack = channel_context_pack_by_id(channel, str(request.get("context_pack_id") or ""), state_dir=state_dir)
    images: list[dict[str, str]] = []
    seen: set[str] = set()
    for ref in pack.get("artifact_refs") or []:
        uri = str(ref.get("uri") or "")
        if ref.get("mime") not in IMAGE_TYPES or not uri.startswith(URI_PREFIX) or uri in seen:
            continue
        body, _ = read_channel_attachment(
            state_dir, ref, channel_id=str(channel.get("channel_id") or request.get("channel_id") or ""),
            thread_id=str(ref.get("thread_id") or "main"),
        )
        images.append({"name": body["name"], "mime": body["mime"], "data": body["data_base64"]})
        seen.add(uri)
    return images
